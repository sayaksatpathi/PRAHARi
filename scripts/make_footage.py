"""Render the simulated cameras to video files.

Produces real, decodable MP4 files rather than in-memory frames. Two reasons
this is worth doing rather than only simulating:

1.  It exercises the **file and RTSP ingestion path** - the same `StreamSource`
    that talks to a real camera - instead of the simulator's in-process shortcut.
    Decode, looping, reconnection and frame timing all become real.

2.  It gives MediaMTX something to serve. Serving these files as genuine RTSP
    endpoints turns "we have a simulator" into "we ingest RTSP", which for a
    problem statement about *existing CCTV infrastructure* is a materially
    stronger claim.

The output is still synthetic imagery and is labelled as such everywhere. To run
against real surveillance footage instead, drop your own files into the same
directory and point the cameras at them - nothing downstream cares which.

Usage:
    .venv/Scripts/python.exe scripts/make_footage.py
    .venv/Scripts/python.exe scripts/make_footage.py --seconds 120 --out footage/
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.config import get_settings  # noqa: E402
from prahari.edge.demo import demo_cameras  # noqa: E402
from prahari.edge.sources.simulator import OpticalProfile, SimulatedCamera  # noqa: E402


def build_simulator(camera, seed: int) -> SimulatedCamera:
    sim_cfg = camera.sim_profile or {}
    preset = sim_cfg.get("preset", "perimeter_aged")
    return SimulatedCamera(
        camera_id=camera.camera_id,
        width=camera.claimed_width,
        height=camera.claimed_height,
        fps=camera.claimed_fps,
        fov_deg=camera.field_of_view_deg,
        camera_height_m=float(sim_cfg.get("camera_height_m", 5.0)),
        tilt_deg=float(sim_cfg.get("tilt_deg", 2.0)),
        optical=OpticalProfile.preset(preset),
        scenario=sim_cfg.get("scenario", camera.role.value),
        seed=seed + abs(hash(camera.camera_id)) % 9973,
        fence_distance_m=float(sim_cfg.get("fence_distance_m", 45.0)),
    )


def render(camera, seconds: float, out_dir: Path, seed: int,
           inject_at: float | None) -> Path | None:
    sim = build_simulator(camera, seed)
    sim.open()

    out_path = out_dir / f"{camera.camera_id}.mp4"
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(out_path), fourcc, sim.nominal_fps,
                             (sim.width, sim.height))
    if not writer.isOpened():
        print(f"  {camera.camera_id}: could not open a writer - skipped")
        return None

    total = int(seconds * sim.nominal_fps)
    injected = False
    started = time.perf_counter()
    written = 0

    try:
        for _ in range(total):
            # Put one scripted subject into the recording partway through, so the
            # footage contains something the rule engine should react to rather
            # than only ambient traffic.
            if (inject_at is not None and not injected
                    and sim.sim_time >= inject_at):
                sim.inject_intruder()
                injected = True

            frame = sim.read()
            if frame is None:
                continue          # a dropped frame, as a flaky camera produces
            writer.write(frame.image)
            written += 1
    finally:
        writer.release()

    elapsed = time.perf_counter() - started
    size_mb = out_path.stat().st_size / 1e6 if out_path.exists() else 0.0
    print(f"  {camera.camera_id:<9} {sim.width}x{sim.height} @ {sim.nominal_fps:.0f} fps  "
          f"{written:>5} frames  {size_mb:6.1f} MB  "
          f"(rendered in {elapsed:.0f}s)"
          + ("  [contains a scripted crossing]" if injected else ""))
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seconds", type=float, default=90.0,
                        help="length of each clip in simulated seconds")
    parser.add_argument("--out", type=Path, default=Path("footage"),
                        help="output directory")
    parser.add_argument("--seed", type=int, default=None,
                        help="override the deterministic seed")
    parser.add_argument("--inject-at", type=float, default=35.0,
                        help="simulated time at which to inject a crossing "
                             "(use -1 for ambient traffic only)")
    args = parser.parse_args()

    settings = get_settings()
    seed = args.seed if args.seed is not None else settings.demo_seed
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    inject_at = None if args.inject_at is not None and args.inject_at < 0 else args.inject_at

    print(f"Rendering {args.seconds:.0f}s per camera into {out_dir}/ (seed {seed})")
    print("This is synthetic imagery. Replace these files with real footage to")
    print("run the same pipeline against genuine surveillance video.\n")

    written = []
    for camera in demo_cameras(settings.node_id):
        path = render(camera, args.seconds, out_dir, seed, inject_at)
        if path:
            written.append(path)

    total_mb = sum(p.stat().st_size for p in written) / 1e6
    print(f"\n{len(written)} file(s), {total_mb:.1f} MB total, in {out_dir}/")
    print("\nNext:")
    print("  Serve them as RTSP      scripts/serve_rtsp.sh")
    print("  Or ingest directly      set a camera's source_kind to \"file\" and")
    print("                          stream_url to the path")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
