"""Public datasets for validating Prahari on real footage.

This script does NOT download anything by default. It lists legitimate public
sources, their licences and their access terms, and downloads only the ones that
are genuinely open and directly fetchable, and only when explicitly named with
--download. Several of the most relevant datasets require registration or a
signed agreement; those are listed but cannot be auto-fetched, by design.

    python scripts/fetch_datasets.py                 # list sources
    python scripts/fetch_datasets.py --download mot17-sample

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

    import time

    import httpx

    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / Path(ds.direct).name

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
            if r.status_code not in (200, 206):
                r.raise_for_status()
            with dest.open(mode) as fh:
                last = t0
                for chunk in r.iter_bytes(1 << 20):
                    fh.write(chunk)
                    got += len(chunk)
                    now = time.perf_counter()
                    if now - last > 5:
                        rate = (got - existing) / 1e6 / (now - t0 + 1e-9)
                        print(f"  {got/1e6:.0f} MB  ({rate:.2f} MB/s)", flush=True)
                        last = now
    except KeyboardInterrupt:
        print(f"\nInterrupted at {got/1e6:.0f} MB. Re-run to resume.")
        return 130
    except Exception as exc:
        print(f"\nDownload failed at {got/1e6:.0f} MB: {exc}")
        print("Re-run the same command to resume from where it stopped.")
        return 1

    print(f"\nSaved {dest} ({dest.stat().st_size/1e6:.1f} MB). Licence: {ds.licence}")
    print("\nUnzip it, then run the harness against it:")
    print(f"    python -c \"import zipfile; zipfile.ZipFile(r'{dest}').extractall(r'{out_dir}')\"")
    print(f"    python scripts/evaluate.py --mot {out_dir} --max-sequences 2")
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
