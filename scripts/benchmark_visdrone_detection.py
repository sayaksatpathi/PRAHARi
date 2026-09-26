"""Aerial / range person-detection benchmark (VisDrone) — border-condition proxy.

Field/border validation needs real border CCTV, which does not exist publicly. As
an honest PROXY for one specific border condition — perimeter surveillance at range
from an elevated camera, where people are small and distant — this runs the
production detector on VisDrone2019-DET, the standard aerial-surveillance dataset,
and reports detection AP@0.5 / precision / recall on the held-out VAL split.

This is NOT real border footage and is labelled as such. It measures how the
detector copes with small/distant people from above — the "range / far-field
perimeter" row of the field-validation acceptance criteria
(docs/field-validation-kit.md).

Dataset: Voxel51/VisDrone2019-DET (Hugging Face), val split (548 images). Person =
the VisDrone 'pedestrians' + 'people' classes. Detector: models/yolox_s.onnx
(COCO, RGB), person class, no aerial fine-tuning.

    python scripts/benchmark_visdrone_detection.py --device cuda
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

PERSON_LABELS = {"pedestrians", "people"}


def load_split_gt(samples_json: Path, split: str):
    data = json.loads(samples_json.read_text(encoding="utf-8"))["samples"]
    gt = {}
    for s in data:
        if split not in s.get("tags", []):
            continue
        boxes = [d["bounding_box"] for d in s["ground_truth"]["detections"]
                 if d.get("label") in PERSON_LABELS]
        gt[s["filepath"]] = boxes
    return gt


def voc_ap(recall: np.ndarray, precision: np.ndarray) -> float:
    mrec = np.concatenate(([0.0], recall, [1.0]))
    mpre = np.concatenate(([0.0], precision, [0.0]))
    for i in range(len(mpre) - 1, 0, -1):
        mpre[i - 1] = max(mpre[i - 1], mpre[i])
    idx = np.where(mrec[1:] != mrec[:-1])[0]
    return float(np.sum((mrec[idx + 1] - mrec[idx]) * mpre[idx + 1]))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=ROOT / "datasets/visdrone")
    ap.add_argument("--model", type=Path, default=ROOT / "models/yolox_s.onnx")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--conf", type=float, default=0.20)
    ap.add_argument("--report-conf", type=float, default=0.35)
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--split", default="val")
    ap.add_argument("--input-size", type=int, default=1280,
                    help="aerial people are tiny; a larger input recovers them")
    ap.add_argument("--out", type=Path, default=ROOT / "var/visdrone_detection_benchmark.json")
    args = ap.parse_args()

    import cv2
    from prahari.edge.detect.onnx_yolo import OnnxYoloDetector

    samples = args.data / "samples.json"
    if not samples.exists():
        print(f"FAIL: {samples} not found (download VisDrone first).")
        return 1
    gt_by_file = load_split_gt(samples, args.split)
    total_expected = sum(len(v) for v in gt_by_file.values())
    print(f"{args.split} split: {len(gt_by_file)} images, {total_expected} person boxes")

    model = args.model
    size = args.input_size
    # yolox_s is 640-native; for the tiny-people aerial case also try yolov8m@1280.
    if size == 1280 and (ROOT / "models/yolov8m_1280.onnx").exists():
        model = ROOT / "models/yolov8m_1280.onnx"
    det = OnnxYoloDetector(model_path=model, device=args.device,
                           conf_threshold=args.conf, input_size=size)
    if "yolov8m" in model.name:
        det._scale_01 = True
        det._swap_rb = True
    print(f"detector: {model.name} @{size} on {det.device.upper()} (no aerial training)")

    all_dets = []
    gt_boxes = {}
    total_gt = 0
    missing = 0
    t0 = time.perf_counter()
    for img_id, (rel, norm) in enumerate(gt_by_file.items()):
        path = args.data / rel
        img = cv2.imread(str(path))
        if img is None:
            missing += 1
            continue
        h, w = img.shape[:2]
        gts = np.array([[x * w, y * h, (x + bw) * w, (y + bh) * h]
                        for (x, y, bw, bh) in norm], float) if norm else np.zeros((0, 4))
        gt_boxes[img_id] = gts
        total_gt += len(gts)
        for d in det.infer(img, allowed=None, frame_index=img_id):
            if d.object_class == ObjectClass.PERSON:
                b = d.bbox
                all_dets.append((float(d.confidence), img_id,
                                 np.array([b.x1, b.y1, b.x2, b.y2], float)))
    runtime = time.perf_counter() - t0

    all_dets.sort(key=lambda x: -x[0])
    matched = {i: set() for i in gt_boxes}
    tp = np.zeros(len(all_dets)); fp = np.zeros(len(all_dets))
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
            matched[img_id].add(best_j); tp[k] = 1
        else:
            fp[k] = 1
        if score >= args.report_conf:
            tp_op += int(is_tp); fp_op += int(not is_tp)

    cum_tp = np.cumsum(tp); cum_fp = np.cumsum(fp)
    recall = cum_tp / max(total_gt, 1)
    precision = cum_tp / np.maximum(cum_tp + cum_fp, 1e-9)
    ap50 = voc_ap(recall, precision)
    prec_op = tp_op / max(tp_op + fp_op, 1)
    rec_op = tp_op / max(total_gt, 1)

    result = {
        "task": "aerial/range person detection (VisDrone) — border-condition PROXY",
        "dataset": f"VisDrone2019-DET {args.split} split (aerial surveillance; NOT "
                   f"real border footage)",
        "detector": f"{model.name} @{size} (COCO/RGB, no aerial training), on {det.device.upper()}",
        "images": len(gt_boxes),
        "gt_person_boxes": total_gt,
        "detections": len(all_dets),
        "metrics": {
            f"AP@{args.iou}": round(ap50, 4),
            f"precision@{args.report_conf}": round(prec_op, 4),
            f"recall@{args.report_conf}": round(rec_op, 4),
        },
        "runtime_sec": round(runtime, 1),
        "proxy_for": "range / far-field perimeter surveillance from an elevated camera",
        "honest_scope": "Aerial proxy, NOT real Indian border footage. Small/distant "
                        "people from above stress the detector the way a far-field "
                        "perimeter camera would. Real border field validation still "
                        "required (docs/field-validation-kit.md).",
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print("\n=== VisDrone aerial/range person-detection (PROXY) ===")
    for k, v in result["metrics"].items():
        print(f"  {k:<18} {v}")
    print(f"  images={len(gt_boxes)} gt={total_gt} dets={len(all_dets)} "
          f"({runtime:.0f}s, {missing} missing)")
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
