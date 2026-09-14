"""Public datasets for validating Prahari on real footage.

This script does NOT download anything by default. It lists legitimate public
sources, their licences and their access terms, and downloads only the ones that
are genuinely open and directly fetchable, and only when explicitly named with
--download. Several of the most relevant datasets require registration or a
signed agreement; those are listed but cannot be auto-fetched, by design.

    python scripts/fetch_datasets.py                 # list sources
    python scripts/fetch_datasets.py --download mot17-sample

A multi-GB resumable download has three ways to silently corrupt its output,
and this script hit the first of them once, costing ~2.5 hours of transfer:

1.  **Two downloaders sharing one file.** A quiet progress log is not a dead
    process. Starting a "resume" alongside a transfer that was actually still
    running gave two writers one file handle; the byte offset stopped matching
    the real content, and every later resume appended at the wrong place. The
    result was a `BadZipFile` 42 MB *larger* than the real archive. A lock file
    now makes the second downloader refuse to start.

2.  **A server that ignores `Range`.** Answering `200` rather than `206` means
    it is resending from byte zero. Appending that to existing bytes yields a
    file of plausible size containing two overlapping copies. The response is
    now checked for `206` *and* a `Content-Range` whose start equals the
    requested offset.

3.  **Writing past the end.** An external "is it big enough yet" check runs
    between attempts, not during one, so an in-flight attempt keeps writing
    after the target size is reached. The transfer now stops at the length the
    server declared, and the finished file is size-verified.

None of the three announces itself. All three produce a file that looks finished.

The honest position this whole tool exists to support: no public dataset
represents Indian border CCTV conditions - night, range, fog, a decade-old
fog-lensed dome. Component benchmarks on these do not transfer, and Prahari makes
no accuracy claim on their basis. They are for exercising the pipeline on real
imagery and characterising failure modes, not for certifying field performance.
That requires authorised footage from the sector concerned, which is a matter for
the sponsoring organisation.
"""
from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Dataset:
    key: str
    name: str
    licence: str
    relevance: str
    access: str            # "open" | "registration" | "agreement"
    url: str
    direct: str | None = None   # a directly fetchable sample, if any


DATASETS = [
    Dataset(
        key="mot17",
        name="MOT17 (Multiple Object Tracking)",
        licence="CC BY-NC-SA 3.0 (research, non-commercial)",
        relevance="Pedestrian detection and tracking with per-frame ground-truth "
                  "boxes - the exact format prahari.eval.mot ingests, and the "
                  "right dataset to produce real detection recall/precision "
                  "through scripts/evaluate.py --mot. Treat it as a tracking "
                  "benchmark, not a border dataset.",
        access="open",
        url="https://motchallenge.net/data/MOT17/",
        # ~5.86 GB. Direct, but see the connection note in download().
        direct="https://motchallenge.net/data/MOT17.zip",
    ),
    Dataset(
        key="mot17-labels",
        name="MOT17 ground-truth labels only",
        licence="CC BY-NC-SA 3.0 (research, non-commercial)",
        relevance="The annotations without the ~5.9 GB of frames. Useful for "
                  "inspecting the GT format the adapter parses, but detection "
                  "cannot be scored without the images.",
        access="open",
        url="https://motchallenge.net/data/MOT17/",
        direct="https://motchallenge.net/data/MOT17Labels.zip",
    ),
    Dataset(
        key="virat",
        name="VIRAT Ground 2.0",
        licence="Public release, research use",
        relevance="Persistent surveillance of people and vehicles in outdoor "
                  "scenes - loitering, entering/exiting, carrying. The closest "
                  "public analogue to the *activity* half of the problem.",
        access="registration",
        url="https://viratdata.org/",
    ),
    Dataset(
        key="ufpr-alpr",
        name="UFPR-ALPR",
        licence="Research use, signed agreement",
        relevance="Real number-plate recognition footage. The right dataset to "
                  "validate the ANPR backend, which is currently wired but "
                  "unvalidated because synthetic plates are not photographs.",
        access="agreement",
        url="https://web.inf.ufpr.br/vri/databases/ufpr-alpr/",
    ),
    Dataset(
        key="aicity",
        name="AI City Challenge",
        licence="Challenge terms, registration",
        relevance="Multi-camera vehicle tracking and re-identification - directly "
                  "relevant to the planned cross-camera handoff and repeat-vehicle "
                  "analysis.",
        access="registration",
        url="https://www.aicitychallenge.org/",
    ),
    Dataset(
        key="anti-uav",
        name="Anti-UAV",
        licence="Research use, registration",
        relevance="Small aerial targets against sky and clutter - the drone "
                  "payload-drop threat that the object class list already "
                  "anticipates but cannot be validated against here.",
        access="registration",
        url="https://anti-uav.github.io/",
    ),
]


def list_datasets() -> None:
    print("Public datasets for validating Prahari on real footage.\n")
    print("None of these represents Indian border CCTV conditions. They exercise")
    print("the pipeline on real imagery and characterise failure modes; they do")
    print("not certify field performance. See the module docstring.\n")
    for d in DATASETS:
        tag = {"open": "OPEN, directly usable",
               "registration": "requires registration",
               "agreement": "requires a signed agreement"}[d.access]
        print(f"  {d.key}")
        print(f"    {d.name}")
        print(f"    licence   : {d.licence}")
        print(f"    access    : {tag}")
        print(f"    relevance : {d.relevance}")
        print(f"    url       : {d.url}")
        print()
    print("Download an openly-fetchable sample with:")
    print("    python scripts/fetch_datasets.py --download <key>")
    print("\nFor the registration/agreement datasets, obtain them through the URL")
    print("above under their terms, then drop the videos into footage/ and run:")
    print("    python scripts/evaluate.py --footage footage/")


def _range_start(content_range: str) -> int | None:
    """First byte offset from a `Content-Range: bytes <start>-<end>/<total>`."""
    try:
        return int(content_range.split()[1].split("-")[0])
    except (IndexError, ValueError):
        return None


def download(key: str, out_dir: Path) -> int:
    ds = next((d for d in DATASETS if d.key == key), None)
    if ds is None:
        print(f"Unknown dataset '{key}'. Run without --download to list options.")
        return 1
    if ds.access != "open" or not ds.direct:
        print(f"{ds.name} is not directly downloadable: {ds.access}.")
        print(f"Obtain it under its terms at {ds.url}, then place the videos in")
        print(f"footage/ and run scripts/evaluate.py --footage footage/.")
        return 2

    import os
    import time

    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / Path(ds.direct).name
    lock = dest.with_suffix(dest.suffix + ".lock")

    # Guard 1: exactly one downloader per destination file. O_EXCL makes the
    # check and the claim a single atomic operation, so two processes starting
    # together cannot both conclude they are the only one.
    try:
        fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        holder = ""
        try:
            holder = lock.read_text().strip()
        except OSError:
            pass
        print(f"A download of {dest.name} is already in progress ({holder}).")
        print(f"Two writers on one file corrupts it silently - see the note at the")
        print(f"top of this script. If that process is genuinely dead, delete")
        print(f"{lock} and re-run.")
        return 3
    with os.fdopen(fd, "w") as fh:
        fh.write(f"pid {os.getpid()} started {time.strftime('%Y-%m-%d %H:%M:%S')}")

    try:
        return _stream(ds, dest, httpx, time)
    finally:
        lock.unlink(missing_ok=True)


def _verify(dest, expected_total, ds) -> int:
    """Size, and for archives readability. Cheap here, expensive to find later."""
    final = dest.stat().st_size
    if expected_total is not None and final != expected_total:
        print(f"\nFinished with {final} bytes but the server declared "
              f"{expected_total}. The file is not trustworthy; delete it and "
              f"re-run.")
        return 1
    if dest.suffix == ".zip":
        import zipfile

        if not zipfile.is_zipfile(dest):
            print(f"\n{dest} is the right size but is not a readable zip. "
                  f"Delete it and re-run.")
            return 1
        print("Archive verified: readable zip of the declared size.")
    print(f"\nSaved {dest} ({final/1e6:.1f} MB). Licence: {ds.licence}")
    return 0


def _stream(ds, dest, httpx, time) -> int:
    """Fetch `ds.direct` into `dest`, resuming and verifying. Holds the lock."""
    # Resume support: MOT17.zip is ~5.9 GB and a single stream is unlikely to
    # survive a flaky link. Pick up from whatever bytes are already on disk.
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    mode = "ab" if existing else "wb"
    if existing:
        print(f"Resuming {ds.name}: {existing/1e6:.0f} MB already on disk.")
    else:
        print(f"Downloading {ds.name} from {ds.direct}")
        print("Large datasets can be multi-GB; on a slow link this can take hours.")
        print("The download resumes if interrupted - just run the command again.\n")

    t0 = time.perf_counter()
    got = existing
    try:
        with httpx.stream("GET", ds.direct, timeout=120, follow_redirects=True,
                          headers=headers) as r:
            # 416 on a resume means the requested offset is at or past the end:
            # the file is already complete. That is success, not failure - and
            # calling it an error makes a retry loop spin on a finished
            # download indefinitely.
            if r.status_code == 416 and existing:
                print(f"{dest.name} is already complete ({existing/1e6:.1f} MB).")
                return _verify(dest, existing, ds)
            if r.status_code not in (200, 206):
                r.raise_for_status()

            # Guard 2: a resume must actually have been honoured. A 200 to a
            # Range request means the server is sending the whole file again;
            # appending that produces a plausibly-sized file containing two
            # overlapping copies, which is worse than failing.
            if existing:
                if r.status_code != 206:
                    print(f"\nThe server ignored the resume request (HTTP "
                          f"{r.status_code}, not 206). Appending a fresh copy to "
                          f"{existing/1e6:.0f} MB of existing data would corrupt "
                          f"the file.")
                    print(f"Delete {dest} and re-run to download from the start.")
                    return 1
                content_range = r.headers.get("content-range", "")
                start = _range_start(content_range)
                if start is not None and start != existing:
                    print(f"\nThe server resumed from byte {start}, but this file "
                          f"has {existing} bytes. Writing there would leave a gap "
                          f"or an overlap.")
                    print(f"Delete {dest} and re-run to download from the start.")
                    return 1

            # Guard 3: know where the end is, and stop there. An in-flight
            # transfer must not keep writing past the declared length just
            # because some outer loop has not noticed yet.
            declared = r.headers.get("content-length")
            expected_total = (existing + int(declared)) if declared else None

            with dest.open(mode) as fh:
                last = t0
                for chunk in r.iter_bytes(1 << 20):
                    if expected_total is not None and got + len(chunk) > expected_total:
                        chunk = chunk[:expected_total - got]
                        if not chunk:
                            break
                    fh.write(chunk)
                    got += len(chunk)
                    now = time.perf_counter()
                    if now - last > 5:
                        rate = (got - existing) / 1e6 / (now - t0 + 1e-9)
                        print(f"  {got/1e6:.0f} MB  ({rate:.2f} MB/s)", flush=True)
                        last = now
                    if expected_total is not None and got >= expected_total:
                        break
    except KeyboardInterrupt:
        print(f"\nInterrupted at {got/1e6:.0f} MB. Re-run to resume.")
        return 130
    except Exception as exc:
        print(f"\nDownload failed at {got/1e6:.0f} MB: {exc}")
        print("Re-run the same command to resume from where it stopped.")
        return 1

    rc = _verify(dest, expected_total, ds)
    if rc:
        return rc
    print("\nUnzip it, then run the harness against it:")
    print(f"    python -c \"import zipfile; zipfile.ZipFile(r'{dest}').extractall(r'{dest.parent}')\"")
    print(f"    python scripts/evaluate.py --mot {dest.parent} --max-sequences 2")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", metavar="KEY", default=None,
                        help="download an openly-fetchable dataset by key")
    parser.add_argument("--out", type=Path, default=Path("footage/real"))
    args = parser.parse_args()

    if args.download:
        return download(args.download, args.out)
    list_datasets()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
