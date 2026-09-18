"""Evaluate Prahari's face detector on the WIDER FACE validation split.

Read-only. Produces a genuine measured AP for the face-detection component and
writes a JSON report. It changes no models and touches no training data.

    python scripts/evaluate_widerface.py
    python scripts/evaluate_widerface.py --limit 200   # quick smoke

**Honesty notes.**
- Detector: the exact same OpenCV Haar cascade and parameters Prahari runs
  (`prahari/edge/detect/face.py`: haarcascade_frontalface_default.xml,
  scaleFactor=1.1, minNeighbors=5, minSize=30). `detect()` returns no scores, so
  here `detectMultiScale(..., outputRejectLevels=True)` is used to expose the
  cascade's internal level weights as a ranking score. Same detector, same
  operating region; the scores only order detections for the PR curve.
- Metric: VOC-style AP@0.5 IoU (all-point interpolation) over every *valid*
  validation face. Faces flagged `invalid==1` are treated as ignore-regions
  (a detection matching one counts as neither TP nor FP).
- This is NOT the official WIDER FACE easy/medium/hard MATLAB protocol. It is a
  single overall AP over all valid val faces, which is stricter than "easy"
  because tiny/occluded/profile faces are kept. Report it as such.
- Validation split only. WIDER FACE test ground truth is withheld; no test claim.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_IMAGES = ROOT / "data/training/face/WIDER-FACE/WIDER_val/images"
DEFAULT_GT = ROOT / "data/training/face/WIDER-FACE/wider_face_split/wider_face_val_bbx_gt.txt"


def parse_gt(gt_path: Path):
    """Yield (image_rel_path, boxes, invalid_flags). Boxes are [x,y,w,h]."""
    lines = gt_path.read_text().splitlines()
    i = 0
    n = len(lines)
    while i < n:
        name = lines[i].strip()
        i += 1
        if not name:
            continue
        count = int(lines[i].strip())
        i += 1
        boxes, invalids = [], []
        # WIDER writes one all-zero row even when count == 0.
        rows = max(count, 1) if count == 0 else count
        for _ in range(rows):
            parts = lines[i].split()
            i += 1
            if count == 0:
                continue
            x, y, w, h = (float(parts[0]), float(parts[1]),
                          float(parts[2]), float(parts[3]))
            invalid = int(parts[7]) if len(parts) > 7 else 0
            if w <= 0 or h <= 0:
                continue
            boxes.append([x, y, w, h])
            invalids.append(invalid)
        yield name, boxes, invalids


def iou_xywh(a, boxes):
    """IoU of box a [x,y,w,h] against Nx4 boxes [x,y,w,h]."""
    if len(boxes) == 0:
        return np.zeros((0,), dtype=np.float32)
    ax1, ay1, aw, ah = a
    ax2, ay2 = ax1 + aw, ay1 + ah
    b = np.asarray(boxes, dtype=np.float32)
    bx1, by1 = b[:, 0], b[:, 1]
    bx2, by2 = b[:, 0] + b[:, 2], b[:, 1] + b[:, 3]
    ix1 = np.maximum(ax1, bx1)
    iy1 = np.maximum(ay1, by1)
    ix2 = np.minimum(ax2, bx2)
    iy2 = np.minimum(ay2, by2)
    iw = np.clip(ix2 - ix1, 0, None)
    ih = np.clip(iy2 - iy1, 0, None)
    inter = iw * ih
    area_a = aw * ah
    area_b = b[:, 2] * b[:, 3]
    union = area_a + area_b - inter
    return np.where(union > 0, inter / union, 0.0)


def voc_ap(rec, prec):
    """VOC all-point interpolation AP."""
    mrec = np.concatenate(([0.0], rec, [1.0]))
    mpre = np.concatenate(([0.0], prec, [0.0]))
    for k in range(len(mpre) - 1, 0, -1):
        mpre[k - 1] = max(mpre[k - 1], mpre[k])
    idx = np.where(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def build_detector(name: str, provider: str, conf: float):
    """Return (label, detect_fn, meta). detect_fn(bgr_img) -> [([x,y,w,h], score)]."""
    name = name.lower()
    if name == "haar":
        cascade_path = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
        cascade = cv2.CascadeClassifier(str(cascade_path))
        assert not cascade.empty(), f"cascade failed to load from {cascade_path}"

        def detect_fn(img):
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            rects, _, weights = cascade.detectMultiScale3(
                gray, scaleFactor=1.1, minNeighbors=5, minSize=(30, 30),
                outputRejectLevels=True,
            )
            out = []
            for r, w in zip(rects, np.asarray(weights).ravel() if len(rects) else []):
                out.append(([float(r[0]), float(r[1]), float(r[2]), float(r[3])], float(w)))
            return out

        meta = {
            "detector": "OpenCV HaarCascade FrontalFace (haarcascade_frontalface_default.xml)",
            "params": {"scaleFactor": 1.1, "minNeighbors": 5, "minSize": 30},
            "provider": "CPU",
            "model_size_bytes": cascade_path.stat().st_size,
            "classification": "BASELINE",
        }
        return "haar", detect_fn, meta

    if name in ("scrfd", "scrfd_500m"):
        import sys
        sys.path.insert(0, str(ROOT))
        from prahari.edge.detect.scrfd import ScrfdFaceDetector
        det = ScrfdFaceDetector(provider=provider, conf_thresh=conf)
        assert det.session is not None, "SCRFD model not loaded"

        def detect_fn(img):
            return [([x, y, w, h], s) for (x, y, w, h, s) in det.detect_scored(img)]

        prov = "GPU (CUDA)" if "CUDA" in (det.provider or "") else "CPU"
        meta = {
            "detector": "InsightFace SCRFD-500M (ONNX, models/scrfd_500m.onnx)",
            "params": {"input_size": 640, "conf_thresh": conf, "nms_thresh": det.nms_thresh},
            "provider": prov,
            "model_size_bytes": det.model_path.stat().st_size,
            "classification": "CANDIDATE",
        }
        return "scrfd", detect_fn, meta

    raise ValueError(f"unknown detector: {name}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--detector", default="haar", choices=["haar", "scrfd", "scrfd_500m"])
    ap.add_argument("--provider", default="auto", help="scrfd only: auto|cpu|cuda")
    ap.add_argument("--conf", type=float, default=0.3, help="scrfd score threshold")
    ap.add_argument("--images", type=Path, default=DEFAULT_IMAGES)
    ap.add_argument("--gt", type=Path, default=DEFAULT_GT)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--limit", type=int, default=0, help="0 = all val images")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    label, detect_fn, meta = build_detector(args.detector, args.provider, args.conf)
    out_path = args.out or (ROOT / f"var/widerface_val_{label}.json")

    det_scores, det_tp, det_fp = [], [], []
    n_gt_valid = n_images = n_dets = 0
    infer_sec = 0.0
    t0 = time.time()

    items = list(parse_gt(args.gt))
    if args.limit:
        items = items[: args.limit]
    total = len(items)

    for idx, (name, boxes, invalids) in enumerate(items):
        img = cv2.imread(str(args.images / name))
        if img is None:
            continue
        n_images += 1
        valid_boxes = [b for b, inv in zip(boxes, invalids) if inv == 0]
        ignore_boxes = [b for b, inv in zip(boxes, invalids) if inv == 1]
        n_gt_valid += len(valid_boxes)

        ti = time.time()
        dets = detect_fn(img)              # timed: inference + pre/post
        infer_sec += time.time() - ti
        n_dets += len(dets)

        matched = [False] * len(valid_boxes)
        # score-desc within image so higher-confidence dets claim GT first
        for box, score in sorted(dets, key=lambda d: -d[1]):
            det_scores.append(score)
            ious = iou_xywh(box, valid_boxes)
            j = int(np.argmax(ious)) if len(ious) else -1
            if j >= 0 and ious[j] >= args.iou and not matched[j]:
                matched[j] = True
                det_tp.append(1); det_fp.append(0)
            else:
                # ignore-region? then drop (neither TP nor FP)
                ig = iou_xywh(box, ignore_boxes)
                if len(ig) and ig.max() >= args.iou:
                    det_scores.pop()
                    continue
                det_tp.append(0); det_fp.append(1)

        if total and (idx + 1) % 500 == 0:
            print(f"  {idx+1}/{total} images  ({(idx+1)/total*100:.0f}%)", flush=True)

    dt = time.time() - t0
    scores = np.asarray(det_scores)
    tp = np.asarray(det_tp)
    fp = np.asarray(det_fp)
    order = np.argsort(-scores)
    tp_c = np.cumsum(tp[order])
    fp_c = np.cumsum(fp[order])
    rec = tp_c / max(n_gt_valid, 1)
    prec = tp_c / np.maximum(tp_c + fp_c, 1)
    ap_val = voc_ap(rec, prec) if len(scores) else 0.0

    total_tp = int(tp.sum())
    recall = total_tp / max(n_gt_valid, 1)
    precision = total_tp / max(int(tp.sum() + fp.sum()), 1)
    ms_per_image = (infer_sec / n_images * 1000) if n_images else 0.0
    fps = (n_images / infer_sec) if infer_sec else 0.0

    result = {
        "dataset": "WIDER FACE",
        "split": "val (official)",
        "detector": meta["detector"],
        "classification": meta["classification"],
        "params": meta["params"],
        "provider": meta["provider"],
        "model_size_mb": round(meta["model_size_bytes"] / 1e6, 2),
        "protocol": "VOC-style AP@0.5 IoU, all valid val faces (NOT official easy/medium/hard)",
        "iou_threshold": args.iou,
        "images_evaluated": n_images,
        "valid_gt_faces": n_gt_valid,
        "detections": n_dets,
        "AP": round(ap_val, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "true_positives": total_tp,
        "latency_ms_per_image": round(ms_per_image, 2),
        "fps": round(fps, 2),
        "wall_runtime_sec": round(dt, 1),
        "note": "Val split only; WIDER FACE test GT withheld -> no test claim. AP is VOC-style over all valid faces, stricter than official 'easy'.",
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    print(f"\nWritten: {out_path}")


if __name__ == "__main__":
    main()
