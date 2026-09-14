"""Cross-camera re-ID evaluation on Market-1501, under the standard protocol.

This measures the thing MOT17 could not. MOT17 is single-camera tracking data:
every sequence is one camera and **no identity appears in two of them**, so a
query/gallery split built from it pairs crops of the same person from the same
camera seconds apart. The HSV histogram scores rank-1 0.966 on that, which says
only that a colour histogram recognises the same person under the same lighting -
true, already known, and not the problem the cross-camera coordinator has.

Market-1501 is built for the real question: 1,501 identities across six cameras,
with the protocol that makes it a cross-camera test -

    for each query, every gallery image of the SAME identity on the SAME
    camera is discarded before ranking

so a correct match *must* come from a different camera, with different
viewpoint, lighting and scale. That is the coordinator's actual job.

Junk handling follows the standard definition: identity `0000` is the
distractor set and `-1` is junk; both are removed from the ranking, as are
same-identity/same-camera images.

    python scripts/evaluate_reid_market.py                  # HSV baseline
    python scripts/evaluate_reid_market.py --model models/reid.onnx
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# 0002_c1s1_000451_03.jpg -> identity 2, camera 1
NAME_RE = re.compile(r"^(-?\d+)_c(\d+)")


class _Box:
    """The minimal box interface both cues expect. Market crops are pre-cut."""

    def __init__(self, w: int, h: int) -> None:
        self.x1, self.y1, self.x2, self.y2 = 0.0, 0.0, float(w), float(h)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1


def listing(directory: Path) -> list[tuple[Path, int, int]]:
    """(path, identity, camera) for every parseable crop in a directory."""
    out = []
    for path in sorted(directory.glob("*.jpg")):
        m = NAME_RE.match(path.name)
        if m:
            out.append((path, int(m.group(1)), int(m.group(2))))
    return out


def vectors_for(items, extractor, batch: int = 256):
    """Feature matrix for a list of crops, using either cue."""
    import cv2

    rows = []
    keep = []
    for start in range(0, len(items), batch):
        chunk = items[start:start + batch]
        images = [cv2.imread(str(p)) for p, _, _ in chunk]
        for (path, pid, cam), image in zip(chunk, images):
            if image is None:
                continue
            vec = extractor(image)
            if vec is not None:
                rows.append(vec)
                keep.append((pid, cam))
    if not rows:
        return np.zeros((0, 1), np.float32), []
    return np.asarray(rows, dtype=np.float32), keep


def histogram_similarity_matrix(queries: np.ndarray, gallery: np.ndarray,
                                chunk: int = 64) -> np.ndarray:
    """Histogram intersection for every query/gallery pair.

    `appearance.similarity` is a scalar Python call, and this protocol needs
    millions of comparisons, so the same arithmetic is done vectorised: the cue
    is normalised per half, and its similarity is the summed elementwise minimum
    halved - exactly what the scalar version computes.
    """
    out = np.empty((len(queries), len(gallery)), dtype=np.float32)
    for start in range(0, len(queries), chunk):
        block = queries[start:start + chunk]
        # (q, 1, d) against (1, g, d) -> summed minimum over d
        out[start:start + chunk] = np.minimum(
            block[:, None, :], gallery[None, :, :]).sum(axis=2) / 2.0
    return out


def score(sim: np.ndarray, query_meta, gallery_meta) -> dict:
    """Rank-1 and mAP under the Market-1501 junk rules."""
    gallery_pid = np.array([p for p, _ in gallery_meta])
    gallery_cam = np.array([c for _, c in gallery_meta])

    rank1 = 0
    average_precisions = []
    counted = 0

    for i, (pid, cam) in enumerate(query_meta):
        # Same identity on the same camera is not a cross-camera match, and the
        # distractor/junk identities are not matches at all. Both are removed
        # from the ranking rather than counted as errors.
        junk = ((gallery_pid == pid) & (gallery_cam == cam)) | \
               (gallery_pid == -1) | (gallery_pid == 0)
        valid = ~junk
        if not valid.any():
            continue
        correct = (gallery_pid == pid) & valid
        if not correct.any():
            continue                     # no cross-camera instance exists

        order = np.argsort(-sim[i][valid], kind="stable")
        hits = correct[valid][order]

        counted += 1
        if hits[0]:
            rank1 += 1
        positions = np.flatnonzero(hits) + 1
        precision = np.arange(1, len(positions) + 1) / positions
        average_precisions.append(float(precision.mean()))

    n = max(1, counted)
    return {
        "queries_scored": counted,
        "gallery": len(gallery_meta),
        "rank1": round(rank1 / n, 4),
        "map": round(float(np.mean(average_precisions)) if average_precisions else 0.0, 4),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path,
                        default=Path("datasets/market1501/Market-1501-v15.09.15"))
    parser.add_argument("--model", type=Path, default=Path("models/reid.onnx"))
    parser.add_argument("--max-queries", type=int, default=600,
                        help="sample this many queries (0 = all 3368). The "
                             "gallery is always used in full.")
    parser.add_argument("--seed", type=int, default=20260915)
    args = parser.parse_args()

    query_dir = args.root / "query"
    gallery_dir = args.root / "bounding_box_test"
    if not query_dir.is_dir() or not gallery_dir.is_dir():
        print(f"Market-1501 not found under {args.root}")
        return 1

    queries = listing(query_dir)
    gallery = listing(gallery_dir)
    if args.max_queries and len(queries) > args.max_queries:
        queries = random.Random(args.seed).sample(queries, args.max_queries)

    cams = sorted({c for _, _, c in gallery})
    print(f"Market-1501: {len(queries)} queries against {len(gallery)} gallery "
          f"crops, cameras {cams}")
    print("Protocol: same-identity/same-camera gallery images discarded, so a "
          "correct match must come from a different camera.\n")

    results = {}

    # --- baseline: the HSV histogram in the pipeline today --------------
    from prahari.edge.crosscam import appearance

    def hsv(image):
        return appearance.signature(image, _Box(image.shape[1], image.shape[0]))

    t0 = time.perf_counter()
    q_vec, q_meta = vectors_for(queries, hsv)
    g_vec, g_meta = vectors_for(gallery, hsv)
    extract_s = time.perf_counter() - t0
    print(f"  HSV: {len(q_meta)} query / {len(g_meta)} gallery vectors "
          f"in {extract_s:.0f}s")
    sim = histogram_similarity_matrix(q_vec, g_vec)
    results["hsv_histogram"] = score(sim, q_meta, g_meta)
    results["hsv_histogram"]["ms_per_crop"] = round(
        extract_s * 1000.0 / max(1, len(q_meta) + len(g_meta)), 3)

    # --- candidate: the trained embedding ------------------------------
    if args.model.exists():
        from prahari.common.config import get_settings
        from prahari.edge.crosscam import reid

        settings = get_settings()
        embedder = reid.ReidEmbedder(
            args.model, device=getattr(settings, "device", "auto"),
            cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))

        def learned(image):
            return embedder.embed(image, _Box(image.shape[1], image.shape[0]))

        t0 = time.perf_counter()
        q_vec, q_meta = vectors_for(queries, learned)
        g_vec, g_meta = vectors_for(gallery, learned)
        extract_s = time.perf_counter() - t0
        print(f"  re-ID: {len(q_meta)} query / {len(g_meta)} gallery embeddings "
              f"in {extract_s:.0f}s on {embedder.device}")
        # Embeddings are L2-normalised, so cosine is a matrix product; mapped to
        # 0..1 the same way reid.similarity does, keeping the scales comparable.
        sim = (q_vec @ g_vec.T + 1.0) / 2.0
        results["reid_embedding"] = score(sim, q_meta, g_meta)
        results["reid_embedding"]["ms_per_crop"] = round(
            extract_s * 1000.0 / max(1, len(q_meta) + len(g_meta)), 3)
        results["reid_embedding"]["device"] = embedder.device
    else:
        print(f"  no model at {args.model} - baseline only\n")

    print(f"\n{'cue':<18}{'rank-1':>9}{'mAP':>9}{'ms/crop':>10}{'device':>9}")
    print("-" * 55)
    for name, r in results.items():
        print(f"{name:<18}{r['rank1']:>9.4f}{r['map']:>9.4f}"
              f"{r['ms_per_crop']:>10.2f}{r.get('device', 'cpu'):>9}")
    print("-" * 55)

    if "reid_embedding" in results:
        base, cand = results["hsv_histogram"], results["reid_embedding"]
        print(f"\nrank-1 {cand['rank1'] - base['rank1']:+.4f}, "
              f"mAP {cand['map'] - base['map']:+.4f}")
        if cand["rank1"] > base["rank1"] and cand["map"] > base["map"]:
            print("VERDICT: the learned cue wins the cross-camera test - integrate.")
        else:
            print("VERDICT: no better cross-camera. Keep the histogram.")

    Path("var").mkdir(exist_ok=True)
    out = Path("var/reid_market_evaluation.json")
    out.write_text(json.dumps({
        "dataset": "Market-1501",
        "protocol": ("Standard: same-identity/same-camera gallery images and the "
                     "0000/-1 distractor identities are discarded before "
                     "ranking, so a correct match must come from a different "
                     "camera."),
        "queries": len(queries), "gallery": len(gallery),
        "results": results,
        "note": ("This is the cross-camera measurement. The MOT17 figure in "
                 "var/reid_evaluation.json is same-camera and is not comparable "
                 "with it."),
    }, indent=2))
    print(f"\nWritten: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
