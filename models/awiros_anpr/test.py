"""
Awiros-ANPR-OCR single-image / directory inference script.

Usage:
    pip install -r requirements.txt
    python test.py --image_path plate.jpg
    python test.py --image_path plates_dir/ --output_json results.json

PaddleOCR repo is needed for model construction. On first run the script
auto-clones it into a PaddleOCR/ subfolder next to this file.
Pass --paddleocr_dir to point to an existing clone instead.
"""

import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path

import cv2
import numpy as np

_SCRIPT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# Model architecture config (PP-OCRv5 server rec, SVTR_HGNet)
# CTC head output: 64 classes (63 dict chars + blank)
# NRTR head output: 68 classes (64 + bos/eos/pad/unk)
# ---------------------------------------------------------------------------
CTC_NUM_CLASSES = 64
NRTR_NUM_CLASSES = 67  # NRTRHead internally adds +1, so 67 -> 68 to match weights

MODEL_CONFIG = {
    "Architecture": {
        "model_type": "rec",
        "algorithm": "SVTR_HGNet",
        "Transform": None,
        "Backbone": {"name": "PPHGNetV2_B4", "text_rec": True},
        "Head": {
            "name": "MultiHead",
            "out_channels_list": {
                "CTCLabelDecode": CTC_NUM_CLASSES,
                "NRTRLabelDecode": NRTR_NUM_CLASSES,
            },
            "head_list": [
                {
                    "CTCHead": {
                        "Neck": {
                            "name": "svtr",
                            "dims": 120,
                            "depth": 2,
                            "hidden_dims": 120,
                            "kernel_size": [1, 3],
                            "use_guide": True,
                        },
                        "Head": {"fc_decay": 1e-05},
                    }
                },
                {"NRTRHead": {"nrtr_dim": 384, "max_text_length": 25}},
            ],
        },
    },
}

IMAGE_SHAPE = [3, 48, 320]
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".tiff", ".tif", ".webp"}


# ---------------------------------------------------------------------------
# PaddleOCR path setup
# ---------------------------------------------------------------------------
def _find_paddleocr(explicit_path=None):
    """Find a directory containing the ppocr package."""
    candidates = []
    if explicit_path:
        candidates.append(Path(explicit_path))
    candidates += [
        _SCRIPT_DIR / "PaddleOCR",
        _SCRIPT_DIR,
        Path.cwd(),
        Path.cwd() / "PaddleOCR",
    ]
    for c in candidates:
        if (c / "ppocr" / "__init__.py").is_file():
            return c
    return None


def _ensure_paddleocr(explicit_path=None):
    """Make ppocr importable. Auto-clones PaddleOCR if not found."""
    root = _find_paddleocr(explicit_path)
    if root is None:
        clone_target = _SCRIPT_DIR / "PaddleOCR"
        print(f"ppocr not found. Cloning PaddleOCR into {clone_target} ...")
        subprocess.check_call([
            "git", "clone", "--depth", "1",
            "https://github.com/PaddlePaddle/PaddleOCR.git",
            str(clone_target),
        ])
        root = clone_target
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    return root


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def parse_args():
    p = argparse.ArgumentParser("Awiros-ANPR-OCR inference")
    p.add_argument("--image_path", required=True,
                    help="Path to a single image or a directory of images.")
    p.add_argument("--weights", default="",
                    help="Path to model.safetensors (default: next to this script).")
    p.add_argument("--dict_path", default="",
                    help="Path to en_dict.txt (default: next to this script).")
    p.add_argument("--device", default="gpu", choices=["gpu", "cpu"],
                    help="Device for inference.")
    p.add_argument("--output_json", default="",
                    help="Optional output JSON path for results.")
    p.add_argument("--paddleocr_dir", default="",
                    help="Path to PaddleOCR repo root (auto-cloned if omitted).")
    return p.parse_args()


def resolve_path(user_path: str, filename: str) -> str:
    """Use user-supplied path if it exists, else fall back to script dir."""
    if user_path and os.path.exists(user_path):
        return user_path
    alt = _SCRIPT_DIR / filename
    if alt.exists():
        return str(alt)
    raise FileNotFoundError(
        f"Could not find {filename}. Place it next to this script or pass its path."
    )


def load_safetensors_to_paddle(paddle_mod, weight_path: str):
    from safetensors.numpy import load_file
    np_state = load_file(weight_path)
    return {k: paddle_mod.to_tensor(v) for k, v in np_state.items()}


def resize_for_rec(img_bgr, target_shape):
    _, h, w = target_shape
    img_h, img_w = img_bgr.shape[:2]
    ratio = h / img_h
    new_w = min(int(img_w * ratio), w)
    resized = cv2.resize(img_bgr, (new_w, h))
    if new_w < w:
        padded = np.zeros((h, w, 3), dtype=np.uint8)
        padded[:, :new_w, :] = resized
        resized = padded
    return resized


def preprocess(img_bgr, target_shape):
    img = resize_for_rec(img_bgr, target_shape)
    img = img.astype(np.float32) / 255.0
    img = (img - 0.5) / 0.5
    return img.transpose((2, 0, 1))


def collect_images(path: str):
    p = Path(path)
    if p.is_file():
        return [p]
    if p.is_dir():
        return sorted(f for f in p.iterdir()
                       if f.is_file() and f.suffix.lower() in IMAGE_EXTENSIONS)
    raise FileNotFoundError(f"Path not found: {path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    args = parse_args()

    # 1. Ensure ppocr is importable, then import paddle + ppocr
    _ensure_paddleocr(args.paddleocr_dir or None)

    import paddle
    from ppocr.modeling.architectures import build_model as ppocr_build_model
    from ppocr.postprocess import build_post_process

    # 2. Device
    if args.device == "gpu" and not paddle.is_compiled_with_cuda():
        print("CUDA not available, falling back to CPU.")
        paddle.set_device("cpu")
    else:
        paddle.set_device(args.device)

    # 3. Resolve file paths
    weights_path = resolve_path(args.weights, "model.safetensors")
    dict_path = resolve_path(args.dict_path, "en_dict.txt")

    # 4. Build CTC post-processor
    post_process = build_post_process({
        "name": "CTCLabelDecode",
        "character_dict_path": dict_path,
        "use_space_char": True,
    })

    # 5. Build model and load weights
    config = copy.deepcopy(MODEL_CONFIG)
    model = ppocr_build_model(config["Architecture"])
    model.eval()

    state_dict = load_safetensors_to_paddle(paddle, weights_path)
    model.set_state_dict(state_dict)
    print(f"Loaded weights from {weights_path}")

    # 6. Run inference
    image_paths = collect_images(args.image_path)
    print(f"Found {len(image_paths)} image(s)\n")

    results = []
    for img_path in image_paths:
        img_bgr = cv2.imread(str(img_path))
        if img_bgr is None:
            print(f"WARNING: Could not read {img_path}, skipping.")
            continue

        tensor = paddle.to_tensor(
            np.expand_dims(preprocess(img_bgr, IMAGE_SHAPE), axis=0)
        )

        with paddle.no_grad():
            preds = model(tensor)

        if isinstance(preds, dict):
            pred_tensor = preds.get("ctc", next(iter(preds.values())))
        elif isinstance(preds, (list, tuple)):
            pred_tensor = preds[0]
        else:
            pred_tensor = preds

        post_result = post_process(pred_tensor.numpy())
        if isinstance(post_result, (list, tuple)) and len(post_result) > 0:
            text, confidence = post_result[0]
        else:
            text, confidence = "", 0.0

        text = text.strip().upper()
        result = {
            "image": str(img_path.name),
            "prediction": text,
            "confidence": round(float(confidence), 4),
        }
        results.append(result)
        print(f"  {img_path.name}: {text}  (conf: {confidence:.4f})")

    # 7. Save JSON
    if args.output_json:
        out_path = Path(args.output_json)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(results, indent=2))
        print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
