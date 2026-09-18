"""Run Prahari's REAL detector over REAL downloaded footage.

Unlike the border-scenario harness (synthetic detector on simulated clips), this
loads the real ONNX YOLO detector (COCO person/vehicle) and runs it on real stock
video, so the detection numbers are about a trained network on real imagery.

Honesty: the clips are generic street/transit stock footage (Pexels), NOT border
footage. Results characterise real-footage detection, not border performance.

    .venv/Scripts/python.exe scripts/test_real_clips.py
"""
from __future__ import annotations

import json
import sys
import time
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prahari.edge.detect.onnx_yolo import OnnxYoloDetector  # noqa: E402

CLIP_DIR = ROOT / "data/testing/real_world"
MODEL = ROOT / "models/yolox_s.onnx"
STRIDE = 3          # infer every Nth frame (speed)
OUT_JSON = ROOT / "var/real_clip_test.json"

COLOURS = {
    "person": (0, 220, 0), "car": (0, 165, 255), "truck": (0, 0, 255),
    "bus": (255, 0, 0), "motorcycle": (255, 0, 255), "bicycle": (255, 255, 0),
    "cattle": (0, 255, 255),
}


def annotate(frame, dets):
    for d in dets:
        b = d.bbox
        c = COLOURS.get(d.object_class.value, (200, 200, 200))
        cv2.rectangle(frame, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)), c, 2)
        cv2.putText(frame, f"{d.object_class.value} {d.confidence:.2f}",
                    (int(b.x1), max(12, int(b.y1) - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, c, 1, cv2.LINE_AA)
    return frame


def main():
    assert MODEL.exists(), f"missing {MODEL}"
    det = OnnxYoloDetector(model_path=MODEL, device="cpu", conf_threshold=0.35)
    print(f"detector: {det.describe().get('name')} on {det.device}, input {det.input_size}")

    clips = sorted(CLIP_DIR.glob("*.mp4"))
    results = []
    for clip in clips:
        cap = cv2.VideoCapture(str(clip))
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps_src = cap.get(cv2.CAP_PROP_FPS)
        class_counts = Counter()
        frame_det_counts = []
        infer_ms = []
        processed = 0
        best_frame = None
        best_n = -1
        idx = 0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % STRIDE == 0:
                t = time.perf_counter()
                dets = det.infer(frame, allowed=None, frame_index=idx)
                infer_ms.append((time.perf_counter() - t) * 1000)
                processed += 1
                frame_det_counts.append(len(dets))
                for d in dets:
                    class_counts[d.object_class.value] += 1
                if len(dets) > best_n:            # keep the busiest frame to annotate
                    best_n = len(dets)
                    best_frame = annotate(frame.copy(), dets)
            idx += 1
        cap.release()

        sample_png = ROOT / f"var/real_clip_sample_{clip.stem}.png"
        if best_frame is not None:
            cv2.imwrite(str(sample_png), best_frame)
        mean_ms = sum(infer_ms) / len(infer_ms) if infer_ms else 0.0
        rec = {
            "clip": clip.name,
            "source_frames": total, "fps_src": round(fps_src, 1),
            "frames_processed": processed, "stride": STRIDE,
            "total_detections": int(sum(class_counts.values())),
            "by_class": dict(class_counts),
            "mean_detections_per_frame": round(
                sum(frame_det_counts) / len(frame_det_counts), 2) if frame_det_counts else 0,
            "peak_detections_in_frame": best_n,
            "latency_ms_per_frame": round(mean_ms, 1),
            "fps": round(1000 / mean_ms, 1) if mean_ms else 0,
            "sample_frame": str(sample_png.relative_to(ROOT)).replace("\\", "/"),
        }
        results.append(rec)
        print(f"  {clip.name}: {rec['total_detections']} dets over {processed} frames, "
              f"classes={rec['by_class']}, {rec['latency_ms_per_frame']}ms/frame "
              f"({rec['fps']} fps), peak {best_n}/frame")

    summary = {
        "status": "REAL footage (Pexels stock) + REAL detector (yolox_s, COCO). "
                  "Generic street/transit scenes, NOT border footage.",
        "detector": "OnnxYoloDetector / models/yolox_s.onnx (COCO person+vehicle), CPU",
        "clips": len(results),
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(summary, indent=2))
    print(f"\nWritten: {OUT_JSON}")


if __name__ == "__main__":
    main()
