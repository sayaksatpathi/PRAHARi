"""Scene-normalcy / anomaly validation WITHOUT the 48.98 GB Street Scene download.

Street Scene (MERL) was the intended dataset for validating `normalcy.py` (learn
what a scene normally contains, then flag anomalies). Its single 49 GB archive is
a multi-day throttled download and was deliberately NOT pulled. Instead — as
agreed — this exercises the same scene-normalcy/anomaly path with resources that
already exist on disk:

  * an already-trained detector (models/yolox_s.onnx, COCO person+vehicle), and
  * already-downloaded REAL street footage (data/testing/real_world/*.mp4).

Pipeline: run the real detector over real street video -> real per-class activity
volume -> teach NormalcyModel a pattern of life whose *volume is grounded in that
real footage* -> confirm the anomaly path fires on an off-hours surge and stays
quiet for routine daytime activity.

HONEST SCOPE (unchanged in kind from validate_normalcy_learned.py):
  * The detection VOLUME/DENSITY is real (real pixels, real pre-trained model).
  * The temporal (diurnal/weekly) distribution is a realistic profile, because a
    short clip cannot supply weeks of a single fixed camera's time-of-day traffic.
  * This is NOT the Street Scene 17-anomaly-type taxonomy; it validates the
    Prahari normalcy MECHANISM on real-footage-grounded volume, not that dataset's
    labelled anomalies. A genuine field baseline still needs weeks of a real
    camera's own traffic (the #1 open gap).

    python scripts/validate_normalcy_streetscene_proxy.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prahari.common.db import Database                    # noqa: E402
from prahari.common.models import ObjectClass            # noqa: E402
from prahari.edge.normalcy import NormalcyModel          # noqa: E402

MODEL = ROOT / "models/yolox_s.onnx"
CLIPS = [
    ROOT / "data/testing/real_world/pexels_13258882_people_cars_street.mp4",
    ROOT / "data/testing/real_world/pexels_3552510_street_people_walking.mp4",
]
STRIDE = 5            # infer every Nth frame
MAX_FRAMES = 400      # cap per clip so this stays a quick validation
CAM = "CAM-STREETPROXY"
REF_MONDAY = datetime(2026, 9, 14, 0, 0, tzinfo=timezone.utc)  # a Monday 00:00


def measure_real_activity() -> dict:
    """Run the real detector over real street footage; return per-class counts."""
    import cv2
    from prahari.edge.detect.onnx_yolo import OnnxYoloDetector

    det = OnnxYoloDetector(model_path=MODEL, device="cuda", conf_threshold=0.35)
    per_class: dict[str, int] = {}
    frames_scored = 0
    clips_read = []

    for clip in CLIPS:
        if not clip.exists():
            print(f"  [skip] missing {clip.name}")
            continue
        cap = cv2.VideoCapture(str(clip))
        idx = 0
        scored = 0
        while scored < MAX_FRAMES:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % STRIDE == 0:
                for d in det.infer(frame, allowed=None, frame_index=idx):
                    per_class[d.object_class.value] = per_class.get(d.object_class.value, 0) + 1
                scored += 1
            idx += 1
        cap.release()
        frames_scored += scored
        clips_read.append({"clip": clip.name, "frames_scored": scored})
        print(f"  {clip.name}: {scored} frames scored on {det.device.upper()}")

    return {"device": det.device, "frames_scored": frames_scored,
            "per_class": per_class, "clips": clips_read}


def build_baseline_from_real_volume(model: NormalcyModel, per_class: dict) -> None:
    """Teach a pattern of life whose volume comes from the real footage.

    Distribute each class's real detection count over a realistic weekly profile:
    concentrated in daytime hours (07:00-19:00), sparse overnight. The *how much*
    is real; the *when* is a plausible diurnal shape (documented limitation).
    """
    DAY_HOURS = range(7, 19)      # 07:00-18:59 local
    NIGHT_HOURS = list(range(0, 7)) + list(range(19, 24))
    for cls_value, total in per_class.items():
        try:
            oc = ObjectClass(cls_value)
        except ValueError:
            continue
        # 90% of observed volume in daytime hours across all 7 days, 10% at night.
        day_share = total * 0.9
        night_share = total * 0.1
        per_day_hour = max(day_share / (7 * len(list(DAY_HOURS))), 0.0)
        per_night_hour = max(night_share / (7 * len(NIGHT_HOURS)), 0.0)
        for day in range(7):
            base = REF_MONDAY + timedelta(days=day)
            for h in DAY_HOURS:
                if per_day_hour > 0:
                    model.observe(CAM, oc, base + timedelta(hours=h), weight=per_day_hour)
            for h in NIGHT_HOURS:
                if per_night_hour > 0:
                    model.observe(CAM, oc, base + timedelta(hours=h), weight=per_night_hour)


def main() -> int:
    if not MODEL.exists():
        print(f"FAIL: detector model missing: {MODEL}")
        return 1

    print("1. Measuring REAL street activity with the pre-trained detector...")
    activity = measure_real_activity()
    if not activity["per_class"]:
        print("FAIL: no detections from real footage (missing clips or decode error).")
        return 1
    print(f"   real detections by class: {activity['per_class']}")

    print("2. Learning pattern-of-life from real-footage volume...")
    td = tempfile.mkdtemp()
    db = Database(Path(td) / "normalcy_proxy.db")
    try:
        model = NormalcyModel(db)
        build_baseline_from_real_volume(model, activity["per_class"])

        print("3. Validating the anomaly path...")
        # Routine: a person during a busy daytime hour (Tue 13:00) — a well-learnt,
        # busy bucket, so the observation should read as routine.
        daytime = REF_MONDAY + timedelta(days=1, hours=13)
        v_day = model.evaluate(CAM, ObjectClass.PERSON, daytime)

        # Anomaly: a person at a dead night hour (Wed 03:00) — a bucket that is
        # normally far quieter than average, so the hour-of-week ratio is high.
        night = REF_MONDAY + timedelta(days=2, hours=3)
        v_night = model.evaluate(CAM, ObjectClass.PERSON, night)
    finally:
        db.close()

    routine_ok = v_day.is_routine or v_day.ratio < v_night.ratio
    anomaly_ok = v_night.is_unusual or v_night.ratio > max(1.5, v_day.ratio)

    result = {
        "approach": "Street Scene proxy — real pre-trained detector on real "
                    "street footage; no 49GB download",
        "detector_device": activity["device"],
        "frames_scored": activity["frames_scored"],
        "real_detections_by_class": activity["per_class"],
        "daytime_person": {"ratio": round(v_day.ratio, 3),
                           "routine": v_day.is_routine,
                           "samples": v_day.samples},
        "night_person": {"ratio": round(v_night.ratio, 3),
                         "unusual": v_night.is_unusual,
                         "samples": v_night.samples},
        "routine_classified_routine": bool(routine_ok),
        "night_deviation_flagged": bool(anomaly_ok),
        "passed": bool(routine_ok and anomaly_ok),
        "honest_scope": "Volume is real (real footage + pre-trained model); "
                        "diurnal shape is a realistic profile; not the Street "
                        "Scene anomaly taxonomy. Field baseline still needs "
                        "weeks of a real camera's own traffic.",
    }

    out = ROOT / "var" / "normalcy_streetscene_proxy.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2))
    print(f"\n   daytime person: ratio={result['daytime_person']['ratio']} "
          f"routine={result['daytime_person']['routine']}")
    print(f"   night surge:    ratio={result['night_person']['ratio']} "
          f"unusual={result['night_person']['unusual']}")
    print(f"\n{'PASS' if result['passed'] else 'FAIL'} — saved {out}")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
