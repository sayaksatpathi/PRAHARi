"""Fetch individual MOT17 sequences, with the ground truth verified against source.

Why this exists alongside `fetch_datasets.py --download mot17`:

**The monolithic archive is 5.86 GB and this link cannot deliver it in one
stream.** Measured on the build machine: a single connection to *any* host
sustains ~46 KB/s, which puts MOT17.zip at roughly 34 hours. Eight concurrent
connections sustain ~607 KB/s aggregate to the same host. The bottleneck is
per-connection throttling, not the pipe - so the fix is many small parallel
requests rather than one large sequential one.

`--max-sequences 2` needs two sequences, not twenty-one. Two sequences fetched
as individual frames is ~300 MB against 5.86 GB, and at the parallel rate that
is minutes rather than a day.

**Provenance, because the frames come from a community mirror.** MOTChallenge
does not serve per-sequence downloads, so the JPEGs are fetched from a
HuggingFace mirror. A third-party copy of a dataset is not automatically the
dataset, so nothing here trusts it:

  * The **ground truth and seqinfo always come from the official
    `MOT17Labels.zip`**, fetched from motchallenge.net and size/zip-verified.
    That file is 10 MB and downloads fine in one stream.
  * The mirror's own `gt.txt` is compared against the official one before its
    frames are accepted. They must match as a sorted multiset of numeric rows -
    the mirror re-serialises (`1.0` for `1`) and re-orders (grouped by track
    rather than by frame), so a byte comparison fails on a correct mirror and a
    numeric one does not.
  * Frame count and the sequence length declared in the official `seqinfo.ini`
    must agree.

If any of those disagree, the sequence is rejected. Measurements are only worth
something if the data underneath them is what it claims to be.

    python scripts/fetch_mot_sequence.py MOT17-02-FRCNN MOT17-04-FRCNN
    python scripts/evaluate.py --mot datasets/MOT17/train --max-sequences 2

Licence: MOT17 is CC BY-NC-SA 3.0 (research, non-commercial). That applies to
the data however it is obtained, mirror included.
"""
from __future__ import annotations

import argparse
import configparser
import sys
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

MIRROR = "https://huggingface.co/datasets/Lekim89/MOT17/resolve/main/train"
OFFICIAL_LABELS = "https://motchallenge.net/data/MOT17Labels.zip"

# Eight was measured, not guessed: it is where aggregate throughput stopped
# improving on this link. Raising it further mostly adds connection overhead,
# and being a considerate client of a free mirror matters too.
WORKERS = 8


def _rows(path_or_text) -> list[tuple[float, ...]]:
    """MOT annotation rows as sorted numeric tuples, order-insensitive."""
    text = (path_or_text.read_text(encoding="utf-8")
            if isinstance(path_or_text, Path) else path_or_text)
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            try:
                out.append(tuple(float(x) for x in line.split(",")))
            except ValueError:
                continue
    return sorted(out)


def ensure_official_labels(out_dir: Path, httpx) -> Path:
    """The official labels archive, extracted. GT never comes from the mirror."""
    labels_zip = out_dir / "MOT17Labels.zip"
    extracted = out_dir / "labels"
    if (extracted / "train").is_dir():
        return extracted

    if not labels_zip.exists() or not zipfile.is_zipfile(labels_zip):
        print(f"Fetching official ground truth from {OFFICIAL_LABELS}")
        out_dir.mkdir(parents=True, exist_ok=True)
        with httpx.stream("GET", OFFICIAL_LABELS, timeout=120,
                          follow_redirects=True) as r:
            r.raise_for_status()
            declared = r.headers.get("content-length")
            total = int(declared) if declared else None
            got = 0
            with labels_zip.open("wb") as fh:
                for chunk in r.iter_bytes(1 << 18):
                    fh.write(chunk)
                    got += len(chunk)
        if total is not None and got != total:
            raise RuntimeError(f"labels archive truncated: {got} of {total} bytes")
        if not zipfile.is_zipfile(labels_zip):
            raise RuntimeError("labels archive is not a readable zip")
        print(f"  official labels verified ({got/1e6:.1f} MB)")

    extracted.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(labels_zip) as z:
        z.extractall(extracted)
    return extracted


def verify_mirror(seq: str, official_gt: Path, httpx) -> None:
    """Reject the mirror unless its annotations match the official ones."""
    r = httpx.get(f"{MIRROR}/{seq}/gt/gt.txt", timeout=120, follow_redirects=True)
    r.raise_for_status()
    mirror_rows = _rows(r.text)
    official_rows = _rows(official_gt)
    if not official_rows:
        raise RuntimeError(f"no official ground truth found for {seq}")
    if mirror_rows != official_rows:
        raise RuntimeError(
            f"{seq}: the mirror's ground truth does not match the official "
            f"annotations ({len(mirror_rows)} rows vs {len(official_rows)}). "
            f"Refusing to use its frames.")
    print(f"  provenance: mirror annotations match official exactly "
          f"({len(official_rows)} rows)")


def fetch_frames(seq: str, count: int, dest: Path, httpx) -> int:
    """Download img1/NNNNNN.jpg in parallel. Skips frames already present."""
    dest.mkdir(parents=True, exist_ok=True)
    wanted = []
    for i in range(1, count + 1):
        target = dest / f"{i:06d}.jpg"
        if target.exists() and target.stat().st_size > 0:
            continue
        wanted.append((i, target))
    if not wanted:
        print(f"  all {count} frames already present")
        return 0

    print(f"  fetching {len(wanted)} of {count} frames with {WORKERS} workers")
    done = 0
    failures: list[str] = []

    def one(item):
        index, target = item
        url = f"{MIRROR}/{seq}/img1/{index:06d}.jpg"
        with httpx.Client(timeout=120, follow_redirects=True) as client:
            for attempt in range(4):
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    # Write via a temp name so an interrupted run never leaves a
                    # half-written JPEG that the resume check would skip.
                    tmp = target.with_suffix(".part")
                    tmp.write_bytes(resp.content)
                    tmp.replace(target)
                    return None
                except Exception as exc:
                    if attempt == 3:
                        return f"{index:06d}.jpg: {exc}"
        return None

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(one, item) for item in wanted]
        for future in as_completed(futures):
            err = future.result()
            if err:
                failures.append(err)
            done += 1
            if done % 100 == 0 or done == len(wanted):
                print(f"    {done}/{len(wanted)}", flush=True)

    if failures:
        print(f"  {len(failures)} frame(s) failed:")
        for f in failures[:5]:
            print(f"    {f}")
    return len(failures)


def fetch_sequence(seq: str, out_dir: Path, httpx) -> int:
    print(f"\n=== {seq} ===")
    labels = ensure_official_labels(out_dir, httpx)
    official_seq = labels / "train" / seq
    official_gt = official_seq / "gt" / "gt.txt"
    official_ini = official_seq / "seqinfo.ini"
    if not official_gt.exists() or not official_ini.exists():
        print(f"  {seq} is not in the official labels archive - check the name")
        return 1

    cfg = configparser.ConfigParser()
    cfg.optionxform = str          # MOT keys are case-sensitive
    cfg.read(official_ini)
    length = int(cfg["Sequence"]["seqLength"])
    width = cfg["Sequence"]["imWidth"]
    height = cfg["Sequence"]["imHeight"]
    print(f"  official seqinfo: {length} frames, {width}x{height}")

    verify_mirror(seq, official_gt, httpx)

    target = out_dir / "MOT17" / "train" / seq
    (target / "gt").mkdir(parents=True, exist_ok=True)
    # Ground truth and seqinfo are copied from the official archive, never the
    # mirror - the mirror only ever supplies pixels.
    (target / "gt" / "gt.txt").write_bytes(official_gt.read_bytes())
    (target / "seqinfo.ini").write_bytes(official_ini.read_bytes())

    failures = fetch_frames(seq, length, target / "img1", httpx)

    present = len(list((target / "img1").glob("*.jpg")))
    if present != length:
        print(f"  INCOMPLETE: {present} of {length} frames. Re-run to continue.")
        return 1
    size_mb = sum(p.stat().st_size for p in (target / "img1").glob("*.jpg")) / 1e6
    print(f"  complete: {present} frames, {size_mb:.0f} MB, at {target}")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sequences", nargs="+",
                        help="e.g. MOT17-02-FRCNN MOT17-04-FRCNN")
    parser.add_argument("--out", type=Path, default=Path("datasets"))
    args = parser.parse_args()

    import httpx

    rc = 0
    for seq in args.sequences:
        try:
            rc |= fetch_sequence(seq, args.out, httpx)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            rc = 1

    if rc == 0:
        print(f"\nReady. Evaluate with:")
        print(f"    python scripts/evaluate.py --mot {args.out / 'MOT17' / 'train'} "
              f"--max-sequences {len(args.sequences)}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
