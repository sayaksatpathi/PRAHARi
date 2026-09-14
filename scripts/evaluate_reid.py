"""Measure a trained re-ID embedding against the HSV histogram it would replace.

The learned cue only earns its place if it is measurably better than the cheap
one already in the pipeline. A model that merely *exists* is a larger
dependency, a longer startup, and one more thing to keep working on an edge node
— none of which is paid for by novelty.

So both cues are scored on the same protocol, on identities neither has seen:

**Rank-1** — for each query crop, is the most similar gallery crop the same
person? This is what the coordinator effectively asks when it picks the best
candidate for a handoff.

**mAP** — mean average precision over all correct matches, not just the top one.
Rank-1 can look healthy while the ranking underneath it is poor, and the
coordinator's threshold behaviour depends on the whole ordering rather than the
single best.

The query and gallery come from **different frames of the same identity**, so a
match requires generalising across pose and time rather than recognising a
near-duplicate crop.

    python scripts/evaluate_reid.py
    python scripts/evaluate_reid.py --model models/reid.onnx
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class _Box:
    """The minimal box interface the two cues expect."""

    def __init__(self, w: int, h: int) -> None:
        self.x1, self.y1, self.x2, self.y2 = 0.0, 0.0, float(w), float(h)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


def load_split(data_dir: Path, split: str, per_identity: int, seed: int):
    """(identity, image) pairs from the held-out identities."""
    import cv2

    rng = random.Random(seed)
    items: dict[str, list[np.ndarray]] = defaultdict(list)
    root = data_dir / split
    for identity_dir in sorted(root.iterdir()):
        if not identity_dir.is_dir():
            continue
        paths = sorted(identity_dir.glob("*.jpg"))
        if len(paths) < 2:
            continue                      # cannot form a query/gallery pair
        if len(paths) > per_identity:
            paths = rng.sample(paths, per_identity)
        for path in paths:
            image = cv2.imread(str(path))
            if image is not None:
                items[identity_dir.name].append(image)
    return {k: v for k, v in items.items() if len(v) >= 2}


def score(vectors: dict[str, list[np.ndarray]], similarity) -> dict:
    """Rank-1 and mAP over a query/gallery split by identity.

    The first crop of each identity is the query; every other crop of every
    identity forms the gallery, so each query is ranked against the whole
    population rather than against its own identity alone.
    """
    gallery: list[tuple[str, np.ndarray]] = []
    queries: list[tuple[str, np.ndarray]] = []
    for identity, vecs in vectors.items():
        queries.append((identity, vecs[0]))
        for v in vecs[1:]:
            gallery.append((identity, v))

    rank1 = 0
    average_precisions = []
    for identity, query in queries:
        scored = sorted(((similarity(query, g), gid) for gid, g in gallery),
                        key=lambda t: t[0], reverse=True)
        if scored and scored[0][1] == identity:
            rank1 += 1
        hits = 0
        precisions = []
        for rank, (_, gid) in enumerate(scored, start=1):
            if gid == identity:
                hits += 1
                precisions.append(hits / rank)
        if precisions:
            average_precisions.append(sum(precisions) / len(precisions))

    n = max(1, len(queries))
    return {
        "queries": len(queries),
        "gallery": len(gallery),
        "identities": len(vectors),
        "rank1": round(rank1 / n, 4),
        "map": round(float(np.mean(average_precisions)) if average_precisions else 0.0, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=Path("datasets/reid"))
    parser.add_argument("--model", type=Path, default=Path("models/reid.onnx"))
    parser.add_argument("--split", default="val")
    parser.add_argument("--per-identity", type=int, default=8)
    parser.add_argument("--seed", type=int, default=20260915)
    args = parser.parse_args()

    if not (args.data / args.split).is_dir():
        print(f"No {args.split} split at {args.data}. "
              f"Run scripts/prepare_reid_dataset.py first.")
        return 1

    print(f"Loading the {args.split} split (identities unseen in training)...")
    images = load_split(args.data, args.split, args.per_identity, args.seed)
    if not images:
        print("No identity had two or more usable crops.")
        return 1
    total = sum(len(v) for v in images.values())
    print(f"  {len(images)} identities, {total} crops\n")

    results = {}

    # --- baseline: the HSV histogram currently in the pipeline ----------
    from prahari.edge.crosscam import appearance

    t0 = time.perf_counter()
    hsv = {}
    for identity, crops in images.items():
        vectors = []
        for crop in crops:
            sig = appearance.signature(crop, _Box(crop.shape[1], crop.shape[0]))
            if sig is not None:
                vectors.append(sig)
        if len(vectors) >= 2:
            hsv[identity] = vectors
    hsv_ms = (time.perf_counter() - t0) * 1000.0 / max(1, total)
    results["hsv_histogram"] = score(hsv, appearance.similarity)
    results["hsv_histogram"]["ms_per_crop"] = round(hsv_ms, 3)

    # --- candidate: the trained embedding ------------------------------
    if args.model.exists():
        from prahari.common.config import get_settings
        from prahari.edge.crosscam import reid

        settings = get_settings()
        embedder = reid.ReidEmbedder(
            args.model, device=getattr(settings, "device", "auto"),
            cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))

        t0 = time.perf_counter()
        learned = {}
        for identity, crops in images.items():
            vectors = []
            for crop in crops:
                vec = embedder.embed(crop, _Box(crop.shape[1], crop.shape[0]))
                if vec is not None:
                    vectors.append(vec)
            if len(vectors) >= 2:
                learned[identity] = vectors
        reid_ms = (time.perf_counter() - t0) * 1000.0 / max(1, total)
        results["reid_embedding"] = score(learned, reid.similarity)
        results["reid_embedding"]["ms_per_crop"] = round(reid_ms, 3)
        results["reid_embedding"]["device"] = embedder.device
    else:
        print(f"No model at {args.model} - baseline only. "
              f"Train one with scripts/train_reid.py.\n")

    # --- report ---------------------------------------------------------
    print(f"{'cue':<18}{'rank-1':>9}{'mAP':>9}{'ms/crop':>10}{'device':>9}")
    print("-" * 55)
    for name, r in results.items():
        print(f"{name:<18}{r['rank1']:>9.4f}{r['map']:>9.4f}"
              f"{r['ms_per_crop']:>10.2f}{r.get('device', 'cpu'):>9}")
    print("-" * 55)
    print(f"{results['hsv_histogram']['identities']} identities, "
          f"{results['hsv_histogram']['queries']} queries against a "
          f"{results['hsv_histogram']['gallery']}-crop gallery")

    if "reid_embedding" in results:
        base, cand = results["hsv_histogram"], results["reid_embedding"]
        d_rank1 = cand["rank1"] - base["rank1"]
        d_map = cand["map"] - base["map"]
        print(f"\nrank-1 {d_rank1:+.4f}, mAP {d_map:+.4f}, "
              f"{cand['ms_per_crop'] / max(base['ms_per_crop'], 1e-6):.1f}x the cost")
        if d_rank1 > 0.05 and d_map > 0.05:
            print("VERDICT: the learned cue is clearly better - worth its cost.")
        elif d_rank1 > 0 and d_map > 0:
            print("VERDICT: better, but narrowly. Weigh against the added "
                  "dependency and startup cost.")
        else:
            print("VERDICT: no better than the histogram. Keep the histogram - "
                  "a learned cue that does not win is just a bigger dependency.")

    Path("var").mkdir(exist_ok=True)
    out = Path("var/reid_evaluation.json")
    out.write_text(json.dumps({
        "results": results,
        "protocol": ("Query and gallery drawn from different frames of the same "
                     "identity, on identities held out of training. Rank-1 is "
                     "what the coordinator asks when choosing a handoff "
                     "candidate; mAP reflects the whole ranking, which its "
                     "threshold behaviour depends on."),
        "limitation": ("Daylight pedestrian imagery. Says nothing about "
                       "visible-to-thermal matching."),
    }, indent=2))
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
