"""Export the correct OSNet Market-1501 Re-ID model to ONNX and verify it.

Fault #3 fix, made deployable. Loads osnet_x1_0 with the real torchreid Market-1501
checkpoint (classifier 751 — the ImageNet-backbone file that shipped as
'osnet_x1_0_market1501.pth', classifier 1000, was the wrong weights), exports to
ONNX at 256x128, and re-evaluates the ONNX under the Market protocol to confirm it
preserves the ~0.947 Rank-1. The resulting models/reid_osnet_market.onnx is a
drop-in for the production ReidEmbedder (same 256x128 + ImageNet-norm preprocessing).

    # 1) fetch the real Market checkpoint (once):
    python -c "import gdown; gdown.download(id='1vduhq5DpN2q1g4fYEZfPI17MJeh9qyrA', output='models/osnet_x1_0_market_real.pth')"
    # 2) export + verify:
    python scripts/export_reid_osnet.py
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import evaluate_reid_market as erm            # listing, score        # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", type=Path, default=ROOT / "models/osnet_x1_0_market_real.pth")
    ap.add_argument("--onnx", type=Path, default=ROOT / "models/reid_osnet_market.onnx")
    ap.add_argument("--root", type=Path,
                    default=ROOT / "datasets/market1501/Market-1501-v15.09.15")
    ap.add_argument("--verify", action="store_true", default=True)
    args = ap.parse_args()

    import torch
    try:
        from torchreid.models import build_model
    except ModuleNotFoundError:
        from torchreid.reid.models import build_model

    if not args.weights.exists():
        print(f"FAIL: {args.weights} not found. Fetch the real Market checkpoint "
              "(gdown id 1vduhq5DpN2q1g4fYEZfPI17MJeh9qyrA).")
        return 1

    model = build_model("osnet_x1_0", num_classes=751, pretrained=False)
    ck = torch.load(args.weights, map_location="cpu", weights_only=False)
    sd = ck.get("state_dict", ck)
    model.load_state_dict(sd, strict=False)
    model.eval()

    dummy = torch.randn(1, 3, 256, 128)
    torch.onnx.export(
        model, dummy, str(args.onnx),
        input_names=["input"], output_names=["embedding"],
        dynamic_axes={"input": {0: "batch"}, "embedding": {0: "batch"}},
        opset_version=12,
    )
    print(f"exported -> {args.onnx} ({args.onnx.stat().st_size/1e6:.1f} MB)")

    if not args.verify or not args.root.is_dir():
        return 0

    # Verify the ONNX under the Market protocol with OSNet preprocessing.
    import cv2
    import onnxruntime as ort
    from prahari.common import cuda
    cuda.prepare()
    sess = ort.InferenceSession(str(args.onnx), providers=cuda.providers_for("cuda"))
    iname = sess.get_inputs()[0].name
    mean = np.array([0.485, 0.456, 0.406], np.float32)
    std = np.array([0.229, 0.224, 0.225], np.float32)

    def embed(items):
        feats, meta = [], []
        B = 256
        for i in range(0, len(items), B):
            chunk = items[i:i + B]
            blob = []
            for p, _, _ in chunk:
                im = cv2.imread(str(p))
                if im is None:
                    continue
                im = cv2.cvtColor(im, cv2.COLOR_BGR2RGB)
                im = cv2.resize(im, (128, 256)).astype(np.float32) / 255.0
                im = (im - mean) / std
                blob.append(im.transpose(2, 0, 1))
            if not blob:
                continue
            out = sess.run(None, {iname: np.ascontiguousarray(np.stack(blob), np.float32)})[0]
            out = out / (np.linalg.norm(out, axis=1, keepdims=True) + 1e-9)
            feats.append(out)
            meta += [(pid, cam) for _, pid, cam in chunk]
        return np.concatenate(feats), meta

    q = erm.listing(args.root / "query")
    g = erm.listing(args.root / "bounding_box_test")
    qf, qm = embed(q)
    gf, gm = embed(g)
    res = erm.score(qf @ gf.T, qm, gm)
    print(f"ONNX verify: Rank-1 {res['rank1']}  mAP {res['map']}")
    out = ROOT / "var/reid_osnet_onnx_verify.json"
    out.write_text(json.dumps({"model": args.onnx.name, **res,
                               "preprocessing": "256x128, RGB, ImageNet norm"}, indent=2))
    print(f"saved -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
