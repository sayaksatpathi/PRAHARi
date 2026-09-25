"""Thermal (LWIR) person-detection benchmark — honest, generic.

Runs the production detector on a REAL thermal person-detection dataset and reports
standard detection metrics (AP@0.5, precision, recall). This answers "does an
RGB-trained detector work on thermal imagery at all?" with a real number.

Dataset: Thermal-Person-Detector (Voxel51/dgural on Hugging Face; sourced from
Roboflow SMART2), CC-BY-4.0, 8,778 thermal images with person bounding boxes,
evaluated on the held-out TEST split (854 images).

Detector: models/yolox_s.onnx (COCO, pre-trained, RGB) — the production detector,
run on thermal frames with no thermal-specific training.

HONEST SCOPE (clearly non-Indian):
  * Generic thermal dataset, not Indian border imagery — no Indian thermal dataset
    or thermal-trained model exists publicly.
  * An RGB-trained detector on thermal is a domain mismatch; the number is the
    honest measure of that mismatch, and the fix is a thermal-specific model
    (named roadmap item). Indian border-thermal validation remains pending field
    data.

    python scripts/benchmark_thermal_detection.py --device cuda
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prahari.common.models import ObjectClass          # noqa: E402
from prahari.eval.anomaly import iou                    # noqa: E402


def load_test_gt(samples_json: Path, split: str = "test") -> dict[str, list[list[float]]]:
    """filepath -> list of normalised [x, y, w, h] person boxes, for one split."""
    data = json.loads(samples_json.read_text(encoding="utf-8"))["samples"]
    gt = {}
    for s in data:
        if split not in s.get("tags", []):
            continue
        boxes = [d["bounding_box"] for d in s["ground_truth"]["detections"]
                 if d.get("label") == "person"]
        gt[s["filepath"]] = boxes
    return gt


def voc_ap(recall: np.ndarray, precision: np.ndarray) -> float:
    """All-point (VOC2010+) average precision."""
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([0.0], precision, [0.0]))
    for i in range(len(mpre) - 1, 0, -1):
        mpre[i - 1] = max(mpre[i - 1], mpre[i])      # monotone decreasing
    idx = np.where(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "datasets/thermal_person")
    ap.add_argument("--model", type=Path, default=ROOT / "models/yolox_s.onnx")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--conf", type=float, default=0.20, help="low floor for the AP sweep")
    ap.add_argument("--report-conf", type=float, default=0.35,
                    help="operating point for precision/recall")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--split", default="test")
    ap.add_argument("--out", type=Path, default=ROOT / "var/thermal_detection_benchmark.json")
    args = ap.parse_args()

    import cv2
    from prahari.edge.detect.onnx_yolo import OnnxYoloDetector

    samples = args.data / "samples.json"
    if not samples.exists():
        print(f"FAIL: {samples} not found (run the dataset download first).")
        return 1
    gt_by_file = load_test_gt(samples, args.split)
    print(f"{args.split} split: {len(gt_by_file)} images, "
          f"{sum(len(v) for v in gt_by_file.values())} person boxes")

    det = OnnxYoloDetector(model_path=args.model, device=args.device,
                           conf_threshold=args.conf)
    print(f"detector: {args.model.name} on {det.device.upper()} (no thermal training)")

    all_dets = []          # (score, image_id, box[x1,y1,x2,y2])
    gt_boxes = {}          # image_id -> np.array of [x1,y1,x2,y2]
    total_gt = 0
    missing = 0
    t0 = time.perf_counter()

    for img_id, (rel, norm_boxes) in enumerate(gt_by_file.items()):
        path = args.data / rel
        img = cv2.imread(str(path))
        if img is None:
            missing += 1
            continue
        h, w = img.shape[:2]
        gts = np.array([[x * w, y * h, (x + bw) * w, (y + bh) * h]
                        for (x, y, bw, bh) in norm_boxes], dtype=float) \
            if norm_boxes else np.zeros((0, 4))
        gt_boxes[img_id] = gts
        total_gt += len(gts)
        for d in det.infer(img, allowed=None, frame_index=img_id):
            if d.object_class == ObjectClass.PERSON:
                b = d.bbox
                all_dets.append((float(d.confidence), img_id,
                                 np.array([b.x1, b.y1, b.x2, b.y2], float)))

    runtime = time.perf_counter() - t0
    n_images = len(gt_boxes)

    # Greedy TP/FP assignment over detections sorted by score (VOC protocol).
    all_dets.sort(key=lambda x: -x[0])
    matched: dict[int, set[int]] = {i: set() for i in gt_boxes}
    tp = np.zeros(len(all_dets))
    fp = np.zeros(len(all_dets))
    # precision/recall at the reporting operating point
    tp_op = fp_op = 0
    for k, (score, img_id, box) in enumerate(all_dets):
        gts = gt_boxes.get(img_id, np.zeros((0, 4)))
        best_iou, best_j = 0.0, -1
        for j in range(len(gts)):
            if j in matched[img_id]:
                continue
            v = iou(box, gts[j])
            if v > best_iou:
                best_iou, best_j = v, j
        is_tp = best_iou >= args.iou and best_j >= 0
        if is_tp:
            matched[img_id].add(best_j)
            tp[k] = 1
        else:
            fp[k] = 1
        if score >= args.report_conf:
            tp_op += int(is_tp)
            fp_op += int(not is_tp)

    cum_tp = np.cumsum(tp)
    cum_fp = np.cumsum(fp)
    recall = cum_tp / max(total_gt, 1)
    precision = cum_tp / np.maximum(cum_tp + cum_fp, 1e-9)
    ap50 = voc_ap(recall, precision)

    prec_op = tp_op / max(tp_op + fp_op, 1)
    rec_op = tp_op / max(total_gt, 1)

    result = {
        "task": "thermal (LWIR) person detection",
        "dataset": f"Thermal-Person-Detector (CC-BY-4.0), {args.split} split "
                   f"(generic, non-Indian)",
        "detector": f"{args.model.name} (COCO, RGB-trained, no thermal training), "
                    f"on {det.device.upper()}",
        "images": n_images,
        "gt_person_boxes": total_gt,
        "detections": len(all_dets),
        "metrics": {
            f"AP@{args.iou}": round(ap50, 4),
            f"precision@{args.report_conf}": round(prec_op, 4),
            f"recall@{args.report_conf}": round(rec_op, 4),
        },
        "runtime_sec": round(runtime, 1),
        "honest_scope": "Generic thermal dataset, not Indian border imagery. An "
                        "RGB-trained detector on thermal is a domain mismatch; this "
                        "number measures that mismatch. The fix is a thermal-specific "
                        "model (roadmap). Indian border-thermal validation pending "
                        "field data.",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print("\n=== Thermal person-detection results ===")
    for k, v in result["metrics"].items():
        print(f"  {k:<18} {v}")
    print(f"  images={n_images} gt_boxes={total_gt} dets={len(all_dets)} "
          f"({runtime:.0f}s, {missing} missing)")
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
