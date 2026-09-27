"""Capture short annotated clips from the running node for the HF Spaces showcase.

Polls each camera's (unauthenticated) snapshot endpoint — which already carries
the real detection/tracking/ANPR/zone overlays the node draws — and encodes a
short, small H.264 clip. No GPU or model is needed to *replay* these, so the
hosted showcase runs free and forever.
"""
import os, time, urllib.request
import cv2, numpy as np, imageio

BASE = "http://127.0.0.1:8420"
OUT = "hf_space/assets"
os.makedirs(OUT, exist_ok=True)
CAMS = ["CAM-011", "CAM-014", "CAM-022", "CAM-031", "CAM-045", "CAM-052"]
SECS, FPS, TARGET_W = 16, 6, 640

for cam in CAMS:
    frames, t0 = [], time.time()
    while time.time() - t0 < SECS:
        try:
            jpg = urllib.request.urlopen(f"{BASE}/api/cameras/{cam}/snapshot", timeout=3).read()
            img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                h, w = img.shape[:2]
                if w > TARGET_W:
                    img = cv2.resize(img, (TARGET_W, int(h * TARGET_W / w)))
                frames.append(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))
        except Exception:
            pass
        time.sleep(1.0 / FPS)
    if frames:
        H, W = frames[0].shape[:2]; H -= H % 2; W -= W % 2
        wr = imageio.get_writer(f"{OUT}/{cam}.mp4", fps=FPS, codec="libx264",
                                pixelformat="yuv420p", macro_block_size=None,
                                output_params=["-movflags", "+faststart"])
        for f in frames:
            wr.append_data(f[:H, :W])
        wr.close()
        sz = os.path.getsize(f"{OUT}/{cam}.mp4") // 1024
        print(f"{cam}: {len(frames)} frames -> {sz} KB")
