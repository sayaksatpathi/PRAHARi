"""Run the Prahari evaluation harness and write a report.

    .venv/Scripts/python.exe scripts/evaluate.py
    .venv/Scripts/python.exe scripts/evaluate.py --only CAM-011,CAM-014
    .venv/Scripts/python.exe scripts/evaluate.py --footage footage/   # file sources
    .venv/Scripts/python.exe scripts/evaluate.py --out var/eval

Produces var/eval/evaluation.json and var/eval/evaluation.html.

By default this evaluates the simulated fleet, which has ground truth, so the
event-level and profiling metrics are real. Detection precision/recall on the
simulated fleet characterise the synthetic detector's modelled behaviour, not a
trained network - the report says so in bold. To measure a real model, install
one at models/yolo.onnx and pass --footage pointing at real recordings.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.config import get_settings  # noqa: E402
from prahari.edge.demo import demo_cameras, demo_zones  # noqa: E402
from prahari.edge.factory import build_detector, build_source  # noqa: E402
from prahari.edge.sources.simulator import SimulatedCamera  # noqa: E402
from prahari.eval.harness import EvaluationHarness  # noqa: E402
from prahari.eval.report import write_reports  # noqa: E402


def build_zones_for(camera, source):
    """Zones from the camera's own projected geometry, as demo mode does."""
    if isinstance(source, SimulatedCamera):
        return demo_zones(camera, source)
    # A file/RTSP source has no projection model here, so fall back to a
    # simulator with the same configured geometry purely to place the zones.
    sim_cfg = camera.sim_profile or {}
    proxy = SimulatedCamera(
        camera_id=camera.camera_id,
        width=camera.claimed_width, height=camera.claimed_height,
        fps=camera.claimed_fps, fov_deg=camera.field_of_view_deg,
        camera_height_m=float(sim_cfg.get("camera_height_m", 5.0)),
        tilt_deg=float(sim_cfg.get("tilt_deg", 2.0)),
        scenario=sim_cfg.get("scenario", "perimeter"),
        fence_distance_m=float(sim_cfg.get("fence_distance_m", 45.0)),
    )
    proxy.open()
    return demo_zones(camera, proxy)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", default="",
                        help="comma-separated camera ids to evaluate")
    parser.add_argument("--footage", type=Path, default=None,
                        help="use file sources from this directory instead of "
                             "the simulator")
    parser.add_argument("--profile-seconds", type=float, default=40.0)
    parser.add_argument("--eval-seconds", type=float, default=45.0)
    parser.add_argument("--out", type=Path, default=Path("var/eval"))
    args = parser.parse_args()

    settings = get_settings()
    detector = build_detector(settings)
    wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}

    print(f"Detector: {detector.describe().get('name')}"
          f"{'  (SIMULATED)' if detector.describe().get('simulated') else ''}")
    print(f"Evaluating {'footage in ' + str(args.footage) if args.footage else 'the simulated fleet'}\n")

    results = []
    for camera in demo_cameras(settings.node_id):
        if wanted and camera.camera_id not in wanted:
            continue

        sim_cfg = camera.sim_profile or {}
        true_height = float(sim_cfg.get("camera_height_m", 5.0))

        if args.footage:
            recording = args.footage / f"{camera.camera_id}.mp4"
            if not recording.exists():
                print(f"  {camera.camera_id}: no recording at {recording} - skipped")
                continue
            camera = camera.model_copy(update={
                "source_kind": "file",
                "stream_url": str(recording.resolve()),
            })
            true_height = None  # unknown geometry for arbitrary footage

        source = build_source(camera, settings)
        fps = camera.claimed_fps
        profile_frames = int(args.profile_seconds * fps)
        eval_frames = int(args.eval_seconds * fps)
        inject_at = profile_frames + int(eval_frames * 0.35)

        def inject(src):
            if hasattr(src, "inject_intruder"):
                src.inject_intruder()

        print(f"  {camera.camera_id}: profiling {profile_frames} frames, "
              f"evaluating {eval_frames}...", flush=True)
        harness = EvaluationHarness(
            camera=camera, source=source, detector=detector,
            zones=build_zones_for(camera, source),
            settings=settings, true_camera_height_m=true_height,
        )
        t0 = time.perf_counter()
        result = harness.run(
            profile_frames=profile_frames,
            eval_frames=eval_frames,
            inject_intruder_at_frame=inject_at if not args.footage else None,
            inject_fn=inject if not args.footage else None,
        )
        d = result.as_dict()
        results.append(d)
        ev = d["events"]
        print(f"    intruder_detected={ev['intruder_detected']} "
              f"latency={ev['detection_latency_s']}s "
              f"false_alarms/h={ev['false_alarms_per_hour']} "
              f"det_recall={d['detection']['recall']:.2f} "
              f"profiling_err={d['profiling']['height_error_percent']}% "
              f"({time.perf_counter()-t0:.0f}s)")

    if not results:
        print("Nothing evaluated.")
        return 1

    meta = {
        "seed": settings.demo_seed,
        "scenario_count": len(results),
        "detector": detector.describe().get("name"),
        "detector_simulated": bool(detector.describe().get("simulated")),
        "source": "footage" if args.footage else "simulator",
    }
    json_path, html_path = write_reports(results, meta, args.out)
    print(f"\nReport written:\n  {json_path}\n  {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
