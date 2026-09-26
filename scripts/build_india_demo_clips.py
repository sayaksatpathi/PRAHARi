"""Stitch real Indian street footage into per-camera >=2-minute demo clips.

Source clips (short, real, CC0 Pexels footage of Indian streets/traffic/crowds)
are concatenated and looped into a >=120 s compilation per demo camera, each
styled to suit that camera: a thermal colormap for the ridge thermal unit and a
degraded low-res look for the legacy analogue feed. Output lands in
data/demo/india/ and is wired up by prahari/edge/demo.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np

RAW = Path("data/demo/india_raw")
OUT = Path("data/demo/india")
OUT.mkdir(parents=True, exist_ok=True)

TARGET_S = 125          # a little over two minutes
OUT_FPS = 25

# camera -> (ordered source ids, style, output size)
PLAN = {
    "CAM-011": (["30381745", "30722588", "26727332", "14063901", "32665228"],
                "normal", (960, 540)),   # main gate: vehicles + people
    "CAM-014": (["29614662", "29214416", "29614667", "29614735"],
                "normal", (960, 540)),   # perimeter: pedestrian flow
    "CAM-022": (["32665228", "32665222", "2843866", "30722588"],
                "normal", (960, 540)),   # open-border approach: mixed traffic
    "CAM-031": (["29614662", "29614667", "29214416"],
                "thermal", (640, 480)),  # ridge thermal
    "CAM-045": (["5069172", "2843866", "26727332"],
                "legacy", (640, 480)),   # legacy analogue south track
}


def style_frame(frame, style, size):
    frame = cv2.resize(frame, size, interpolation=cv2.INTER_AREA)
    if style == "thermal":
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        frame = cv2.applyColorMap(gray, cv2.COLORMAP_INFERNO)
    elif style == "legacy":
        # Soften, desaturate a touch and add mild sensor grain for an aged feed.
        frame = cv2.GaussianBlur(frame, (3, 3), 0)
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.int16)
        hsv[..., 1] = (hsv[..., 1] * 0.6).astype(np.int16)
        frame = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
        noise = np.random.normal(0, 6, frame.shape).astype(np.int16)
        frame = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return frame


def build(cam, ids, style, size):
    target = TARGET_S * OUT_FPS
    out_path = OUT / f"{cam}.mp4"
    writer = cv2.VideoWriter(str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
                             OUT_FPS, size)
    written = 0
    loops = 0
    while written < target:
        loops += 1
        for vid in ids:
            src = RAW / f"{vid}.mp4"
            if not src.exists():
                continue
            cap = cv2.VideoCapture(str(src))
            while written < target:
                ok, frame = cap.read()
                if not ok:
                    break
                writer.write(style_frame(frame, style, size))
                written += 1
            cap.release()
            if written >= target:
                break
        if loops > 50:            # safety valve
            break
    writer.release()
    print(f"{cam}: {written} frames -> {written / OUT_FPS:.0f}s  {size}  {style}  "
          f"({out_path}, {out_path.stat().st_size // 1024} KB)")


def main():
    only = sys.argv[1:] or list(PLAN)
    for cam in only:
        ids, style, size = PLAN[cam]
        build(cam, ids, style, size)


if __name__ == "__main__":
    main()
