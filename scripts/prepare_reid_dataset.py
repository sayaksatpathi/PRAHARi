"""Build a person re-identification dataset from MOT17 ground truth.

Cross-camera handoff currently matches on an HSV colour histogram
(`prahari/edge/crosscam/appearance.py`). That is cheap, needs no download and is
genuinely discriminative at a distance, but it is the documented weak link: a
learned embedding should do better, and MOT17 carries per-frame identities, so
the training data is already on disk.

Three things about this data decide how the extraction works.

**Consecutive frames are not independent samples.** One person over 600 frames is
600 near-identical crops, not 600 examples. Left unchecked, the identity with the
longest track dominates the loss and the model learns that person rather than the
task. Crops are therefore subsampled in time (`--stride`) and capped per identity
(`--max-per-id`).

**Track ids are sequence-local.** `track_id 1` in MOT17-02 and `track_id 1` in
MOT17-04 are different people. Identities are namespaced by sequence, or the
model is taught to collapse two strangers into one class.

**The split must be by identity, not by image.** Splitting randomly across crops
puts the same person in train and validation, and the validation accuracy then
measures memorisation. Re-identification is explicitly the task of generalising
to people never seen in training, so the held-out identities are disjoint.

    python scripts/prepare_reid_dataset.py --out datasets/reid
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Standard re-ID crop geometry: people are roughly twice as tall as wide.
CROP_W, CROP_H = 64, 128
# Below this a crop carries no appearance information worth learning from - it is
# a smear of a few pixels, and training on it teaches noise.
MIN_W, MIN_H = 32, 64


def extract(mot_root: Path, out_dir: Path, stride: int, max_per_id: int,
            val_fraction: float, seed: int) -> int:
    import cv2

    from prahari.eval.mot import load_gt

    rng = random.Random(seed)
    by_identity: dict[str, list[tuple[Path, list[float]]]] = defaultdict(list)

    sequences = sorted(d for d in mot_root.iterdir()
                       if (d / "gt" / "gt.txt").exists() and (d / "img1").is_dir())
    if not sequences:
        print(f"No MOT sequences with ground truth under {mot_root}")
        return 1

    for seq in sequences:
        gt = load_gt(seq / "gt" / "gt.txt")
        kept = 0
        for frame_index in sorted(gt):
            if frame_index % stride:
                continue
            image_path = seq / "img1" / f"{frame_index:06d}.jpg"
            if not image_path.exists():
                continue
            for entry in gt[frame_index]:
                x1, y1, x2, y2 = entry["bbox"]
                if (x2 - x1) < MIN_W or (y2 - y1) < MIN_H:
                    continue
                # Namespaced: track ids only mean anything within one sequence.
                identity = f"{seq.name}_{entry['track_id']}"
                by_identity[identity].append((image_path, [x1, y1, x2, y2]))
                kept += 1
        print(f"  {seq.name:<20} {kept:>6} candidate crops")

    # Cap per identity so a long track cannot dominate, sampling across the whole
    # track rather than taking the first N - the first N are one pose in one
    # place, which is the opposite of the variation the model needs.
    for identity, items in by_identity.items():
        if len(items) > max_per_id:
            by_identity[identity] = rng.sample(items, max_per_id)

    # Identities with too few crops cannot support a train/val notion of the same
    # person looking different, so they are dropped rather than padded.
    identities = sorted(i for i, items in by_identity.items() if len(items) >= 4)
    rng.shuffle(identities)
    n_val = max(1, int(len(identities) * val_fraction))
    val_ids = set(identities[:n_val])
    train_ids = [i for i in identities if i not in val_ids]

    print(f"\n  {len(identities)} identities with >= 4 crops")
    print(f"  train {len(train_ids)} identities | val {len(val_ids)} identities "
          f"(disjoint - re-ID must generalise to unseen people)")

    manifest: dict[str, list[dict]] = {"train": [], "val": []}
    frame_cache: dict[Path, "object"] = {}
    written = 0

    for split, ids in (("train", train_ids), ("val", sorted(val_ids))):
        for label, identity in enumerate(ids):
            target_dir = out_dir / split / identity
            target_dir.mkdir(parents=True, exist_ok=True)
            for n, (image_path, box) in enumerate(sorted(by_identity[identity])):
                image = frame_cache.get(image_path)
                if image is None:
                    image = cv2.imread(str(image_path))
                    # A tiny cache: crops are grouped by identity, so the same
                    # frame recurs across nearby entries but not globally.
                    if len(frame_cache) > 24:
                        frame_cache.clear()
                    frame_cache[image_path] = image
                if image is None:
                    continue
                x1, y1, x2, y2 = (int(round(v)) for v in box)
                x1, y1 = max(0, x1), max(0, y1)
                x2 = min(image.shape[1], x2)
                y2 = min(image.shape[0], y2)
                if x2 - x1 < MIN_W or y2 - y1 < MIN_H:
                    continue
                crop = cv2.resize(image[y1:y2, x1:x2], (CROP_W, CROP_H),
                                  interpolation=cv2.INTER_LINEAR)
                out_path = target_dir / f"{n:04d}.jpg"
                cv2.imwrite(str(out_path), crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
                manifest[split].append({
                    "path": str(out_path.relative_to(out_dir)),
                    "identity": identity, "label": label,
                })
                written += 1
        print(f"  {split}: {len(manifest[split])} crops")

    (out_dir / "manifest.json").write_text(json.dumps({
        "crop_size": [CROP_W, CROP_H],
        "train_identities": len(train_ids),
        "val_identities": len(val_ids),
        "counts": {k: len(v) for k, v in manifest.items()},
        "source": "MOT17 train, ground-truth boxes",
        "note": ("Identity-disjoint split. All crops are daylight street "
                 "pedestrians; this dataset cannot teach visible-to-thermal "
                 "matching, which is the harder half of the cross-camera "
                 "problem at a border."),
        "items": manifest,
    }, indent=2))

    print(f"\n  {written} crops written to {out_dir}")
    print(f"  manifest: {out_dir / 'manifest.json'}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mot", type=Path, default=Path("datasets/MOT17/train"))
    parser.add_argument("--out", type=Path, default=Path("datasets/reid"))
    parser.add_argument("--stride", type=int, default=6,
                        help="keep every Nth frame; consecutive crops are near-"
                             "duplicates (default 6)")
    parser.add_argument("--max-per-id", type=int, default=120,
                        help="cap per identity so long tracks cannot dominate")
    parser.add_argument("--val-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260915)
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    return extract(args.mot, args.out, args.stride, args.max_per_id,
                   args.val_fraction, args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
