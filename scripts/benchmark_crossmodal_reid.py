"""Cross-modal (visible <-> thermal) Re-ID measurement — honest, generic.

The audit asks for visible<->thermal Re-ID validation. The standard cross-modal
Re-ID datasets (RegDB, SYSU-MM01) are research-gated (copyright form / email), and
there is no publicly redistributable one, so a true LWIR cross-modal Rank-1/mAP
cannot be produced in-session without violating dataset terms. What CAN be measured
honestly, on data already on disk, is the thing that actually breaks cross-modal
Re-ID: the loss of the colour cue.

This runs the production Re-ID model (models/reid.onnx, trained on VISIBLE
Market-1501) under the standard Market-1501 protocol, twice:

  * visible query  -> visible gallery   (the normal same-modality baseline)
  * synthetic-thermal query -> visible gallery   (cross-modal proxy)

The synthetic-thermal transform removes colour (grayscale, replicated to 3
channels) — the dominant cue a visible Re-ID model relies on and exactly what LWIR
thermal lacks. The Rank-1/mAP drop quantifies the modality gap for the current
model.

HONEST SCOPE (clearly non-Indian, clearly a proxy):
  * Generic dataset (Market-1501, Chinese campus), not Indian — no Indian Re-ID
    data exists publicly.
  * "Synthetic thermal" = colour removed. It captures the loss-of-colour half of
    the visible<->thermal gap; it does NOT reproduce true LWIR texture/appearance
    inversion. A real LWIR number needs RegDB/SYSU (gated) or field data.
  * The result is expected to be a large drop — that is the honest finding: a
    visible-trained Re-ID model does not transfer to thermal, which is why a
    thermal-specific / cross-modal model is a named roadmap item.

    python scripts/benchmark_crossmodal_reid.py
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

# Reuse the verified Market-1501 harness (listing, vectors_for, score, _Box).
import evaluate_reid_market as erm            # noqa: E402


def synth_thermal(image):
    """Colour-removed (grayscale) proxy for a thermal frame."""
    import cv2
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path,
                    default=ROOT / "datasets/market1501/Market-1501-v15.09.15")
    ap.add_argument("--model", type=Path, default=ROOT / "models/reid.onnx")
    ap.add_argument("--max-queries", type=int, default=600)
    ap.add_argument("--seed", type=int, default=20260915)
    args = ap.parse_args()

    query_dir = args.root / "query"
    gallery_dir = args.root / "bounding_box_test"
    if not query_dir.is_dir() or not gallery_dir.is_dir():
        print(f"Market-1501 not found under {args.root}")
        return 1
    if not args.model.exists():
        print(f"Re-ID model not found: {args.model}")
        return 1

    queries = erm.listing(query_dir)
    gallery = erm.listing(gallery_dir)
    if args.max_queries and len(queries) > args.max_queries:
        queries = random.Random(args.seed).sample(queries, args.max_queries)
    print(f"Market-1501: {len(queries)} queries / {len(gallery)} gallery crops")

    from prahari.common.config import get_settings
    from prahari.edge.crosscam import reid

    settings = get_settings()
    embedder = reid.ReidEmbedder(
        args.model, device=getattr(settings, "device", "auto"),
        cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))

    def visible(image):
        return embedder.embed(image, erm._Box(image.shape[1], image.shape[0]))

    def thermal(image):
        t = synth_thermal(image)
        return embedder.embed(t, erm._Box(t.shape[1], t.shape[0]))

    # Gallery is always visible (the enrolled daytime identities).
    t0 = time.perf_counter()
    g_vec, g_meta = erm.vectors_for(gallery, visible)
    print(f"  gallery: {len(g_meta)} visible embeddings ({time.perf_counter()-t0:.0f}s "
          f"on {embedder.device})")

    results = {}
    for name, extractor in (("visible_query", visible), ("thermal_query", thermal)):
        t0 = time.perf_counter()
        q_vec, q_meta = erm.vectors_for(queries, extractor)
        sim = (q_vec @ g_vec.T + 1.0) / 2.0
        r = erm.score(sim, q_meta, g_meta)
        r["extract_s"] = round(time.perf_counter() - t0, 1)
        results[name] = r
        print(f"  {name:<14} rank-1 {r['rank1']:.4f}  mAP {r['map']:.4f}")

    base, cross = results["visible_query"], results["thermal_query"]
    drop_r1 = round(base["rank1"] - cross["rank1"], 4)
    drop_map = round(base["map"] - cross["map"], 4)

    out = {
        "task": "cross-modal (visible<->thermal) Re-ID, honest proxy",
        "dataset": "Market-1501 (generic, non-Indian; standard cross-camera protocol)",
        "model": args.model.name + " (trained on VISIBLE data)",
        "device": embedder.device,
        "synthetic_thermal": "colour removed (grayscale->3ch); proxy for the "
                             "loss-of-colour half of the visible<->thermal gap, "
                             "not true LWIR appearance",
        "visible_query": {k: base[k] for k in ("rank1", "map", "queries_scored")},
        "thermal_query": {k: cross[k] for k in ("rank1", "map", "queries_scored")},
        "modality_gap": {"rank1_drop": drop_r1, "map_drop": drop_map},
        "finding": ("A visible-trained Re-ID model loses substantial accuracy when "
                    "the query loses colour, quantifying the modality gap. A "
                    "thermal-specific / cross-modal Re-ID model is required; a true "
                    "LWIR number needs a gated VT-Re-ID dataset (RegDB/SYSU) or "
                    "Indian field data (pending)."),
        "honest_scope": "Generic dataset, not Indian. Synthetic-thermal (colour "
                        "removed) is a proxy, not real LWIR. Lower bound on the gap.",
    }
    (ROOT / "var").mkdir(exist_ok=True)
    outp = ROOT / "var/crossmodal_reid_benchmark.json"
    outp.write_text(json.dumps(out, indent=2))
    print(f"\n  modality gap: rank-1 {drop_r1:+.4f}, mAP {drop_map:+.4f}")
    print(f"  (visible {base['rank1']:.3f} -> thermal-proxy {cross['rank1']:.3f})")
    print(f"saved -> {outp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
