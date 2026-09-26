"""Correct OSNet Market-1501 Re-ID evaluation — the SOTA path for fault #3.

The earlier drop-in (reid_osnet.onnx) measured Rank-1 0.14 — a preprocessing/export
mismatch, not the weights. This loads OSNet through torchreid's own FeatureExtractor
(model_name='osnet_x1_0', the 2019 Market-1501 checkpoint), which applies the
correct preprocessing (256x128, RGB, ImageNet mean/std), and evaluates under the
standard Market-1501 protocol. It should reproduce the published ~0.94 Rank-1 and,
if so, is the honest fix for "weak Re-ID (0.705)".

    python scripts/benchmark_reid_osnet_correct.py
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_reid_market as erm            # listing, score        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=Path,
                    default=ROOT / "datasets/market1501/Market-1501-v15.09.15")
    ap.add_argument("--weights", type=Path, default=ROOT / "models/osnet_x1_0_market1501.pth")
    ap.add_argument("--max-queries", type=int, default=0, help="0 = all 3368")
    ap.add_argument("--seed", type=int, default=20260915)
    ap.add_argument("--out", type=Path, default=ROOT / "var/reid_osnet_correct.json")
    args = ap.parse_args()

    q_dir = args.root / "query"
    g_dir = args.root / "bounding_box_test"
    if not q_dir.is_dir() or not g_dir.is_dir():
        print(f"Market-1501 not found under {args.root}")
        return 1
    if not args.weights.exists():
        print(f"OSNet weights not found: {args.weights}")
        return 1

    import torch
    try:
        from torchreid.utils import FeatureExtractor
    except ModuleNotFoundError:
        from torchreid.reid.utils import FeatureExtractor

    queries = erm.listing(q_dir)
    gallery = erm.listing(g_dir)
    if args.max_queries and len(queries) > args.max_queries:
        queries = random.Random(args.seed).sample(queries, args.max_queries)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    extractor = FeatureExtractor(model_name="osnet_x1_0",
                                 model_path=str(args.weights), device=device)
    print(f"OSNet x1_0 on {device}; {len(queries)} queries / {len(gallery)} gallery")

    def embed(items):
        paths = [str(p) for p, _, _ in items]
        feats = []
        B = 256
        for i in range(0, len(paths), B):
            f = extractor(paths[i:i + B])          # (b, 512) torch tensor
            f = torch.nn.functional.normalize(f, dim=1)
            feats.append(f.cpu().numpy())
        return np.concatenate(feats, axis=0), [(pid, cam) for _, pid, cam in items]

    t0 = time.perf_counter()
    qf, qm = embed(queries)
    gf, gm = embed(gallery)
    extract_s = time.perf_counter() - t0

    sim = qf @ gf.T                                # cosine (already normalised)
    res = erm.score(sim, qm, gm)
    res["ms_per_crop"] = round(extract_s * 1000 / max(1, len(qm) + len(gm)), 3)
    res["device"] = device

    out = {
        "task": "cross-camera Re-ID (Market-1501) — OSNet x1_0, correct pipeline",
        "dataset": "Market-1501 (standard protocol; same-id/same-cam + 0000/-1 discarded)",
        "model": "osnet_x1_0 (torchreid Market-1501 checkpoint)",
        "rank1": res["rank1"], "map": res["map"],
        "queries_scored": res["queries_scored"], "gallery": res["gallery"],
        "device": device, "ms_per_crop": res["ms_per_crop"],
        "baseline_resnet18": {"rank1": 0.705, "map": 0.485},
        "note": "Loaded via torchreid FeatureExtractor (256x128, RGB, ImageNet "
                "norm). Supersedes the earlier reid_osnet.onnx drop-in (0.14), "
                "which was a preprocessing/export mismatch, not the weights.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2))
    print(f"\nOSNet Market-1501: Rank-1 {res['rank1']}  mAP {res['map']}  "
          f"(ResNet-18 baseline 0.705 / 0.485)")
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
