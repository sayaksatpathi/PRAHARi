"""Smoke test: does profiling recover real geometry from nothing but observation?

Runs each simulated camera preset, feeds the profiler frames plus the person
bounding boxes a detector would produce, and checks the self-calibrated ground
plane against the simulator's known truth. If the recovered camera height does
not match the height the simulator was configured with, the DORI banding built
on top of it is meaningless.

Run:  .venv/Scripts/python.exe scripts/smoke_profiling.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.models import (  # noqa: E402
    Camera, CameraRole, Capability, SensorType,
)
from prahari.edge.profiling.certificate import issue_certificate, verify_certificate  # noqa: E402
from prahari.edge.profiling.measure import CameraProfiler  # noqa: E402
from prahari.edge.sources.simulator import OpticalProfile, SimulatedCamera  # noqa: E402

CASES = [
    # (label, preset, role, fov, cam height, width, height, scenario, tilt)
    # The gate camera is short-throw and tilted down, as a real ANPR install is.
    # Everything else is near-level, looking out at range.
    ("Gate camera, new, narrow FOV", "gate_hd", CameraRole.CHOKEPOINT, 28.0, 3.0, 1280, 720, "chokepoint", 12.0),
    ("Perimeter dome, aged", "perimeter_aged", CameraRole.PERIMETER, 62.0, 6.0, 1280, 720, "perimeter", 2.0),
    ("Approach camera, night IR", "night_ir", CameraRole.APPROACH, 45.0, 5.0, 1280, 720, "approach", 4.0),
    ("Thermal perimeter", "thermal", CameraRole.PERIMETER, 50.0, 6.0, 640, 480, "perimeter", 2.0),
    ("Legacy analogue, degraded", "degraded_legacy", CameraRole.APPROACH, 70.0, 4.5, 704, 480, "approach", 3.0),
]


def run_case(label, preset, role, fov, cam_h, w, h, scenario, tilt) -> dict:
    cam_id = label.split(",")[0].replace(" ", "-").upper()
    sim = SimulatedCamera(
        camera_id=cam_id, width=w, height=h, fps=12.0, fov_deg=fov,
        camera_height_m=cam_h, optical=OpticalProfile.preset(preset),
        scenario=scenario, seed=20260913, tilt_deg=tilt,
    )
    sim.open()

    profiler = CameraProfiler(cam_id, min_frames=25)
    frames_read = 0
    # 400 frames at 12 fps is a ~33 s profiling window - enough foot traffic to
    # fit the ground plane without making an operator wait.
    for _ in range(400):
        frame = sim.read()
        if frame is None:
            continue
        frames_read += 1
        profiler.observe_frame(frame.image, frame.timestamp.timestamp())
        for gt in frame.ground_truth:
            if gt["object_class"] != "person":
                continue
            x1, y1, x2, y2 = gt["bbox"]
            # Reject boxes clipped by the frame edge: their apparent height is
            # wrong and would corrupt the fit.
            if y2 >= h - 2 or y1 <= 1 or x1 <= 1 or x2 >= w - 2:
                continue
            profiler.observe_person(foot_v=y2, px_height=y2 - y1,
                                    px_width=x2 - x1)

    m = profiler.build(claimed_fps=sim.nominal_fps)

    camera = Camera(
        camera_id=cam_id, name=label, role=role,
        claimed_width=w, claimed_height=h, claimed_fps=12.0,
        field_of_view_deg=fov,
        sensor_type=SensorType.THERMAL if preset == "thermal" else SensorType.VISIBLE,
    )
    cert = issue_certificate(camera, m)

    # --- truth comparison ---------------------------------------------
    recovered_h = None
    if m.ground_plane_estimated and m.horizon_y is not None and m.px_per_metre_near > 0:
        recovered_h = (h - 1 - m.horizon_y) / m.px_per_metre_near

    print("=" * 78)
    print(label)
    print("-" * 78)
    print(f"  frames sampled          : {m.frames_sampled} (source produced {frames_read})")
    print(f"  measured fps            : {m.measured_fps:.1f}  (claimed {m.claimed_fps:.0f})")
    print(f"  sharpness (var Laplace) : {m.sharpness:.0f}")
    print(f"  effective res factor    : {m.effective_resolution_factor:.4f}")
    print(f"  noise sigma             : {m.noise_sigma:.2f}")
    print(f"  block artifacts         : {m.compression_artifact_score:.3f}")
    print(f"  mean luma               : {m.mean_luma:.0f}  low-light={m.is_low_light}")
    print()
    print(f"  GROUND PLANE            : {'RECOVERED' if m.ground_plane_estimated else 'not recovered'}")
    if m.ground_plane_estimated:
        print(f"    horizon row (truth)   : {m.horizon_y:.0f}  (simulator: {sim.horizon_y:.0f})")
        print(f"    camera height (truth) : {recovered_h:.2f} m  (simulator: {cam_h:.2f} m)")
        err = abs(recovered_h - cam_h) / cam_h * 100
        print(f"    height error          : {err:.1f}%")
        print(f"    px/m near / far       : {m.px_per_metre_near:.0f} / {m.px_per_metre_far:.0f}")
    print()
    print(f"  overall DORI            : {cert.overall_dori.value}")
    print(f"  digest verifies         : {verify_certificate(cert)}")
    print()
    print("  CAPABILITY GRANTS")
    for g in cert.grants:
        if g.capability in (Capability.FACE_RECOGNITION,) or g.granted:
            mark = "GRANTED " if g.granted else "REFUSED "
            print(f"    [{mark}] {g.capability.value:<20} {g.reason[:88]}")
    refused_interesting = [
        g for g in cert.grants
        if not g.granted and g.capability in (
            Capability.ANPR, Capability.FACE_DETECTION, Capability.SPEED_ESTIMATION,
            Capability.PERSON_DETECTION, Capability.UAV_DETECTION)
    ]
    for g in refused_interesting:
        print(f"    [REFUSED] {g.capability.value:<20} {g.reason[:88]}")
    if m.notes:
        print()
        print("  MEASUREMENT NOTES")
        for n in m.notes:
            print(f"    - {n}")
    print()

    return {
        "label": label,
        "ground_plane": m.ground_plane_estimated,
        "height_error_pct": (abs(recovered_h - cam_h) / cam_h * 100) if recovered_h else None,
        "anpr": cert.is_granted(Capability.ANPR),
        "person": cert.is_granted(Capability.PERSON_DETECTION),
    }


def main() -> int:
    results = [run_case(*c) for c in CASES]

    print("=" * 78)
    print("SUMMARY")
    print("=" * 78)
    ok = True
    for r in results:
        gp = "yes" if r["ground_plane"] else "NO"
        err = f"{r['height_error_pct']:.1f}%" if r["height_error_pct"] is not None else "n/a"
        print(f"  {r['label']:<34} ground-plane={gp:<4} height-err={err:<7} "
              f"person={r['person']} anpr={r['anpr']}")
        if r["height_error_pct"] is not None and r["height_error_pct"] > 20:
            ok = False
    print()
    print("Expectation: the narrow-FOV gate camera is the only one that should be")
    print("granted ANPR. If a 62-degree perimeter dome is being granted plate")
    print("reading, the profiling is not doing its job.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
