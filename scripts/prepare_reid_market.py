"""Arrange Market-1501's training split into the layout `train_reid.py` expects.

Market-1501 already ships the split that matters: 751 identities in
`bounding_box_train` and a disjoint 750 in the test set used by
`evaluate_reid_market.py`. No identity crosses that line, so the evaluation is
already on people the model has never seen and there is nothing here to get
wrong by re-splitting.

What this does is regroup the flat directory into one folder per identity, which
is what `torchvision.datasets.ImageFolder` reads, and hold out a slice of the
*training* identities for a during-training sanity check. That held-out slice is
not the reported result - the reported result is the Market protocol against the
official test split, which is what `evaluate_reid_market.py` runs.

    python scripts/prepare_reid_market.py
    python scripts/train_reid.py --data datasets/reid_market --epochs 25
    python scripts/evaluate_reid_market.py --model models/reid.onnx
"""
from __future__ import annotations

import argparse
import json
import random
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

NAME_RE = re.compile(r"^(-?\d+)_c(\d+)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path,
                        default=Path("datasets/market1501/Market-1501-v15.09.15"))
    parser.add_argument("--out", type=Path, default=Path("datasets/reid_market"))
    parser.add_argument("--val-identities", type=int, default=50,
                        help="training identities held aside for a sanity check")
    parser.add_argument("--seed", type=int, default=20260915)
    args = parser.parse_args()

    source = args.root / "bounding_box_train"
    if not source.is_dir():
        print(f"Market-1501 training split not found at {source}")
        return 1

    by_identity: dict[int, list[Path]] = defaultdict(list)
    cameras_per_identity: dict[int, set[int]] = defaultdict(set)
    for path in sorted(source.glob("*.jpg")):
        m = NAME_RE.match(path.name)
        if not m:
            continue
        pid, cam = int(m.group(1)), int(m.group(2))
        if pid in (-1, 0):                     # distractor / junk
            continue
        by_identity[pid].append(path)
        cameras_per_identity[pid].add(cam)

    # An identity seen on only one camera teaches nothing about matching across
    # cameras, which is the whole task, so it is dropped.
    usable = {pid: paths for pid, paths in by_identity.items()
              if len(cameras_per_identity[pid]) >= 2 and len(paths) >= 4}
    print(f"  {len(by_identity)} identities in bounding_box_train")
    print(f"  {len(usable)} appear on 2+ cameras with 4+ crops - usable")
    multi = sum(len(cameras_per_identity[p]) for p in usable) / max(1, len(usable))
    print(f"  mean cameras per usable identity: {multi:.1f}")

    identities = sorted(usable)
    random.Random(args.seed).shuffle(identities)
    n_val = min(args.val_identities, max(1, len(identities) // 10))
    val_ids = set(identities[:n_val])

    if args.out.exists():
        shutil.rmtree(args.out)
    counts = {"train": 0, "val": 0}
    for pid in identities:
        split = "val" if pid in val_ids else "train"
        target = args.out / split / f"{pid:04d}"
        target.mkdir(parents=True, exist_ok=True)
        for path in usable[pid]:
            shutil.copyfile(path, target / path.name)
            counts[split] += 1

    (args.out / "manifest.json").write_text(json.dumps({
        "source": "Market-1501 bounding_box_train",
        "train_identities": len(identities) - len(val_ids),
        "val_identities": len(val_ids),
        "counts": counts,
        "note": ("Val here is a slice of the TRAINING identities, for a "
                 "during-training sanity check only. The reported result is the "
                 "Market protocol on the official disjoint test split - see "
                 "scripts/evaluate_reid_market.py."),
        "limitation": ("Daylight RGB across six cameras. Trains genuine "
                       "cross-camera matching; does not address "
                       "visible-to-thermal, which needs SYSU-MM01 or RegDB."),
    }, indent=2))

    print(f"\n  train {counts['train']} crops / {len(identities) - len(val_ids)} identities")
    print(f"  val   {counts['val']} crops / {len(val_ids)} identities")
    print(f"  -> {args.out}")
    print("\nNext (needs the GPU):")
    print("    python scripts/train_reid.py --data datasets/reid_market --epochs 25")
    print("    python scripts/evaluate_reid_market.py --model models/reid.onnx")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
