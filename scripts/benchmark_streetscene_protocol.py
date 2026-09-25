"""Street Scene-protocol video-anomaly benchmark on a real labelled dataset.

Produces the SAME metrics the official Street Scene benchmark reports — frame-level
ROC-AUC, RBDC and TBDC (Ramachandra & Jones, WACV 2020) — computed by the same
criteria, on a real labelled anomaly dataset with pixel-level ground truth.

Because the 48.98 GB Street Scene archive was deliberately not downloaded, the
benchmark runs on **UCSD Ped2** — the canonical video-anomaly-detection dataset
that Street Scene's own paper evaluates against with these exact criteria (~a few
hundred MB, real anomalies: bikes/carts/cars/skaters on a pedestrian walkway,
pixel-mask ground truth for every test clip). The evaluation protocol is
identical; the test videos are the standard VAD benchmark rather than MERL's 35
Street Scene clips.

Anomaly detector: **object-centric, using an already-trained model**
(`models/yolox_s.onnx`, COCO). On a pedestrian walkway the anomalies ARE
non-pedestrian objects, so every detected non-person object is an anomaly region
scored by its detection confidence; pedestrians are normal. This is a standard,
published object-centric VAD approach and uses only a pre-trained model already on
disk — no anomaly-specific training, no large download.

    python scripts/benchmark_streetscene_protocol.py            # auto-find dataset, GPU
    python scripts/benchmark_streetscene_protocol.py --data <UCSDped2 dir> --device cuda
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

from prahari.common.models import ObjectClass                       # noqa: E402
from prahari.eval.anomaly import (                                  # noqa: E402
    Track, frame_level_auc, link_tracks, rbdc_tbdc, regions_from_mask,
)

# COCO classes that are anomalous on a pedestrian walkway (non-pedestrian objects).
ANOMALY_CLASSES = {
    ObjectClass.BICYCLE, ObjectClass.CAR, ObjectClass.MOTORCYCLE,
    ObjectClass.BUS, ObjectClass.TRUCK,
}


def find_ped2(explicit: Path | None) -> Path | None:
    if explicit and (explicit / "Test").is_dir():
        return explicit
    for base in [ROOT / "datasets", ROOT / "data"]:
        for cand in base.rglob("UCSDped2"):
            if (cand / "Test").is_dir():
                return cand
    return None


def list_test_clips(ped2: Path) -> list[tuple[Path, Path]]:
    """(frames_dir, gt_dir) for each test clip that has a pixel-mask GT folder."""
    test = ped2 / "Test"
    clips = []
    for d in sorted(test.iterdir()):
        if d.is_dir() and d.name.startswith("Test") and not d.name.endswith("_gt"):
            gt = test / f"{d.name}_gt"
            if gt.is_dir():
                clips.append((d, gt))
    return clips


def load_frames(frames_dir: Path) -> list[Path]:
    return sorted([p for p in frames_dir.iterdir()
                   if p.suffix.lower() in (".tif", ".tiff", ".jpg", ".png", ".bmp")])


def load_gt_masks(gt_dir: Path) -> list[Path]:
    return sorted([p for p in gt_dir.iterdir()
                   if p.suffix.lower() in (".bmp", ".png", ".tif", ".jpg")])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=None, help="UCSDped2 directory")
    ap.add_argument("--model", type=Path, default=ROOT / "models/yolox_s.onnx")
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--conf", type=float, default=0.20,
                    help="low floor so the score sweep has range")
    ap.add_argument("--out", type=Path, default=ROOT / "var/streetscene_protocol_benchmark.json")
    args = ap.parse_args()

    import cv2
    from prahari.edge.detect.onnx_yolo import OnnxYoloDetector

    ped2 = find_ped2(args.data)
    if ped2 is None:
        print("FAIL: UCSDped2 not found. Extract UCSD_Anomaly_Dataset under datasets/ "
              "or pass --data <UCSDped2 dir>.")
        return 1
    print(f"dataset: {ped2}")
    clips = list_test_clips(ped2)
    print(f"test clips with pixel-mask GT: {len(clips)}")
    if not clips:
        print("FAIL: no test clips with *_gt mask folders found.")
        return 1

    det = OnnxYoloDetector(model_path=args.model, device=args.device,
                           conf_threshold=args.conf)
    print(f"detector: {args.model.name} on {det.device.upper()} "
          f"(anomaly = non-pedestrian object)")

    # Global (flat) accumulators across all clips, with global frame indices.
    frame_scores: list[float] = []
    frame_labels: list[int] = []
    gt_regions_per_frame: list[list[np.ndarray]] = []
    pred_per_frame: list[list[tuple[np.ndarray, float]]] = []
    all_tracks: list[Track] = []

    t0 = time.perf_counter()
    gidx = 0
    for frames_dir, gt_dir in clips:
        frames = load_frames(frames_dir)
        masks = load_gt_masks(gt_dir)
        n = min(len(frames), len(masks))
        clip_gt_regions: list[list[np.ndarray]] = []
        clip_global_start = gidx
        for i in range(n):
            img = cv2.imread(str(frames[i]))
            if img is None:
                continue
            # --- prediction: non-person detections are anomalies ---
            dets = det.infer(img, allowed=None, frame_index=i)
            preds = []
            best = 0.0
            for d in dets:
                if d.object_class in ANOMALY_CLASSES:
                    b = d.bbox
                    box = np.array([b.x1, b.y1, b.x2, b.y2], float)
                    preds.append((box, float(d.confidence)))
                    best = max(best, float(d.confidence))
            pred_per_frame.append(preds)

            # --- ground truth from the pixel mask ---
            mask = cv2.imread(str(masks[i]), cv2.IMREAD_GRAYSCALE)
            gt_regions = regions_from_mask(mask) if mask is not None else []
            gt_regions_per_frame.append(gt_regions)
            clip_gt_regions.append(gt_regions)

            frame_scores.append(best)
            frame_labels.append(1 if gt_regions else 0)
            gidx += 1

        # Link GT tracks WITHIN this clip, then re-key to global frame indices.
        clip_tracks = link_tracks(clip_gt_regions)
        for tr in clip_tracks:
            all_tracks.append(Track({clip_global_start + f: box
                                     for f, box in tr.boxes.items()}))
        print(f"  {frames_dir.name}: {n} frames, "
              f"{sum(len(r) for r in clip_gt_regions)} GT regions, "
              f"{len(clip_tracks)} tracks")

    runtime = time.perf_counter() - t0

    auc = frame_level_auc(frame_scores, frame_labels)
    rt = rbdc_tbdc(gt_regions_per_frame, all_tracks, pred_per_frame)

    result = {
        "protocol": "Street Scene evaluation protocol (Ramachandra & Jones, WACV 2020): "
                    "frame-level ROC-AUC + RBDC + TBDC",
        "dataset": "UCSD Ped2 (standard VAD benchmark; Street Scene paper evaluates "
                   "with these same criteria). Official Street Scene archive (49GB) "
                   "deliberately not downloaded.",
        "detector": f"object-centric, {args.model.name} (COCO, pre-trained), "
                    f"non-pedestrian object = anomaly, on {det.device.upper()}",
        "frames_scored": len(frame_scores),
        "anomalous_frames": int(sum(frame_labels)),
        "test_clips": len(clips),
        "metrics": {
            "frame_auc": round(auc, 4),
            "rbdc": round(rt["rbdc"], 4),
            "tbdc": round(rt["tbdc"], 4),
        },
        "gt_regions": rt["gt_regions"],
        "gt_tracks": rt["gt_tracks"],
        "runtime_sec": round(runtime, 1),
        "honest_scope": "Identical evaluation protocol and metrics to official "
                        "Street Scene, on the standard UCSD Ped2 benchmark rather "
                        "than MERL's 35 Street Scene clips. Object-centric detector "
                        "catches vehicle/bike/cart anomalies well and misses "
                        "'unusual pedestrian' anomalies (detected as person) — a "
                        "known, reported property of object-centric VAD.",
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2))
    print("\n=== Street Scene-protocol results (UCSD Ped2) ===")
    print(f"  Frame-level AUC : {result['metrics']['frame_auc']}")
    print(f"  RBDC            : {result['metrics']['rbdc']}")
    print(f"  TBDC            : {result['metrics']['tbdc']}")
    print(f"  frames={result['frames_scored']} anomalous={result['anomalous_frames']} "
          f"GT regions={result['gt_regions']} tracks={result['gt_tracks']} "
          f"({result['runtime_sec']}s)")
    print(f"saved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
