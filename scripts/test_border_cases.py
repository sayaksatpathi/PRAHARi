"""Run Prahari's perception modules on a real video, per border case.

Real border CCTV is sensitive/unavailable, so this tests the modules on separate
real public clips, one per case (night, vehicle+plate, crowd/group, animal, etc.).
For one clip it runs and reports every perception module the pipeline uses:

    detection (person/vehicle/animal) · tracking (ByteTrack) · ANPR (on vehicles)
    · segmentation / SAM 2 (on the top person box) · face detection (YuNet)

The reasoning modules (rule engine, normalcy, patrol suppression, cross-camera,
evidence hash chain) run inside the live pipeline and have their own validations;
this harness covers the perception stack on real pixels.

    python scripts/test_border_cases.py --video <clip.mp4> --case night_movement
    python scripts/test_border_cases.py --all        # every clip in the manifest
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

from prahari.common.models import BBox, ObjectClass          # noqa: E402

VEHICLE = {ObjectClass.CAR, ObjectClass.TRUCK, ObjectClass.BUS, ObjectClass.MOTORCYCLE}


def run_clip(video: Path, case: str, stride: int, max_frames: int,
             device: str) -> dict:
    import cv2
    from prahari.common.config import get_settings
    from prahari.edge.detect.onnx_yolo import OnnxYoloDetector
    from prahari.edge.track.bytetrack import ByteTracker
    from prahari.edge.segment.factory import build_segmenter
    from prahari.edge.detect.face import build_face_detector
    from datetime import datetime, timezone

    settings = get_settings()
    det = OnnxYoloDetector(model_path=ROOT / "models/yolox_s.onnx",
                           device=device, conf_threshold=0.35)
    tracker = ByteTracker(case, match_iou=settings.track_match_iou,
                          max_lost_frames=30)
    segmenter = build_segmenter(settings)     # SAM 2 if weights present else GrabCut
    face = build_face_detector("auto")

    # ANPR: real fast-alpr if available, else the synthetic reader (labelled).
    anpr = None
    try:
        from prahari.edge.anpr import FastAlprReader
        anpr = FastAlprReader(device=device)
    except Exception as exc:                    # noqa: BLE001
        anpr_note = f"fast-alpr unavailable: {str(exc)[:80]}"
    else:
        anpr_note = "fast-alpr"

    cap = cv2.VideoCapture(str(video))
    counts = {}
    track_ids = set()
    max_concurrent = 0
    plates = []
    masks = []           # (area_fraction, latency_ms)
    seg_backend = segmenter.describe().get("name") if segmenter else "none"
    faces_found = 0
    frames = 0
    idx = 0
    t0 = time.perf_counter()

    while frames < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % stride != 0:
            idx += 1
            continue
        idx += 1
        frames += 1
        h, w = frame.shape[:2]
        now = datetime.now(timezone.utc)
        dets = det.infer(frame, allowed=None, frame_index=frames)
        for d in dets:
            counts[d.object_class.value] = counts.get(d.object_class.value, 0) + 1

        # tracking
        tracks = tracker.update(dets, now, now.timestamp())
        persons = [t for t in tracks if t.object_class is ObjectClass.PERSON]
        for t in persons:
            track_ids.add(t.track_id)
        max_concurrent = max(max_concurrent, len(persons))

        # ANPR on the most confident vehicle
        if anpr is not None:
            veh = [d for d in dets if d.object_class in VEHICLE]
            if veh:
                v = max(veh, key=lambda d: d.confidence)
                try:
                    pr = anpr.read(frame, v.bbox)
                    if pr and getattr(pr, "text", ""):
                        plates.append({"text": pr.text,
                                       "conf": round(float(getattr(pr, "confidence", 0)), 3)})
                except Exception:               # noqa: BLE001
                    pass

        # segmentation (SAM) on the most confident person — every 5th sampled frame
        if segmenter is not None and frames % 5 == 0:
            ppl = [d for d in dets if d.object_class is ObjectClass.PERSON]
            if ppl:
                p = max(ppl, key=lambda d: d.confidence)
                try:
                    seg = segmenter.segment(frame, p.bbox)
                    if seg is not None:
                        area = float(np.count_nonzero(seg.mask)) / max(1, seg.mask.size)
                        masks.append((round(area, 4), round(getattr(seg, "latency_ms", 0), 1)))
                except Exception:               # noqa: BLE001
                    pass

        # face detection on person crops
        for d in dets:
            if d.object_class is ObjectClass.PERSON:
                b = d.bbox
                x1, y1 = max(0, int(b.x1)), max(0, int(b.y1))
                x2, y2 = min(w, int(b.x2)), min(h, int(b.y2))
                if x2 > x1 and y2 > y1:
                    if face.detect(frame[y1:y2, x1:x2]):
                        faces_found += 1
                    break   # one crop per frame is enough for a domain check

    cap.release()
    runtime = time.perf_counter() - t0

    return {
        "case": case,
        "video": video.name,
        "frames_scored": frames,
        "device": det.device,
        "detection": {"by_class": counts},
        "tracking": {"unique_person_tracks": len(track_ids),
                     "max_concurrent_persons": max_concurrent},
        "anpr": {"backend": anpr_note, "plate_reads": len(plates),
                 "samples": plates[:10]},
        "segmentation": {"backend": seg_backend, "masks_produced": len(masks),
                         "mean_area_fraction": round(float(np.mean([m[0] for m in masks])), 4) if masks else 0.0,
                         "mean_latency_ms": round(float(np.mean([m[1] for m in masks])), 1) if masks else 0.0},
        "face": {"frames_with_a_face": faces_found},
        "runtime_sec": round(runtime, 1),
    }


def load_manifest() -> list[dict]:
    mf = ROOT / "data/testing/border_cases/manifest.json"
    if mf.exists():
        return json.loads(mf.read_text())
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--video", type=Path)
    ap.add_argument("--case", default="unlabelled")
    ap.add_argument("--all", action="store_true", help="run every clip in the manifest")
    ap.add_argument("--stride", type=int, default=5)
    ap.add_argument("--max-frames", type=int, default=200)
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--out", type=Path, default=ROOT / "var/border_cases_results.json")
    args = ap.parse_args()

    jobs = []
    if args.all:
        for entry in load_manifest():
            v = ROOT / entry["path"]
            if v.exists():
                jobs.append((v, entry["case"]))
            else:
                print(f"[skip] missing {entry['path']}")
    elif args.video:
        jobs.append((args.video, args.case))
    else:
        print("pass --video <clip> --case <name>, or --all")
        return 1

    results = []
    for v, case in jobs:
        print(f"\n=== {case}  ({v.name}) ===")
        r = run_clip(v, case, args.stride, args.max_frames, args.device)
        results.append(r)
        d = r["detection"]["by_class"]
        print(f"  frames {r['frames_scored']} on {r['device']} | det {d}")
        print(f"  tracks {r['tracking']['unique_person_tracks']} person "
              f"(max {r['tracking']['max_concurrent_persons']} concurrent)")
        print(f"  ANPR ({r['anpr']['backend']}): {r['anpr']['plate_reads']} reads "
              f"{[p['text'] for p in r['anpr']['samples'][:5]]}")
        print(f"  SAM/{r['segmentation']['backend']}: {r['segmentation']['masks_produced']} masks, "
              f"mean area {r['segmentation']['mean_area_fraction']}, "
              f"{r['segmentation']['mean_latency_ms']}ms")
        print(f"  face: {r['face']['frames_with_a_face']} frames with a face")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2))
    print(f"\nsaved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
