"""Demo site: a deliberately heterogeneous camera fleet.

The point of the fleet is that the cameras are *not* alike. A real border outpost
has whatever was installed over the last decade - a new gate camera, an ageing
perimeter dome, a thermal unit on the ridge, and at least one legacy analogue
feed nobody wants to talk about. Prahari's central claim is that it treats them
differently, so the demo has to contain cameras that genuinely deserve different
treatment.

Two doctrines are represented side by side, because the problem statement is
jurisdiction-neutral and India's borders are not one problem: fenced cameras run
tripwire and restricted-zone rules, while the open-border camera runs
pattern-of-life rules against a lawful route.

Zones are generated from each camera's actual projected geometry rather than
hardcoded, so the fence line in the image is the fence line the rules test
against. Hardcoded normalised coordinates drift out of alignment the moment a
camera's field of view or mounting changes, and a tripwire that is not on the
fence is worse than no tripwire.

Everything here is clearly demo scaffolding. `docs/demo.md` states which parts
are simulated, and the dashboard badges them.
"""
from __future__ import annotations

import logging
from pathlib import Path

from prahari.common.config import Settings
from prahari.common.db import Database
from prahari.common.models import (
    BorderProfile,
    Camera,
    CameraRole,
    ObjectClass,
    Point,
    SensorType,
    Zone,
    ZoneKind,
)
from prahari.edge.normalcy import NormalcyModel
from prahari.edge.sources.simulator import SimulatedCamera

log = logging.getLogger("prahari.demo")

# Real Indian street footage for the live-camera demo (CC0 Pexels clips of Indian
# traffic/crowds, stitched into >=2-minute per-camera compilations by
# scripts/build_india_demo_clips.py). Each is looped by StreamSource and the real
# ONNX detector runs on it, so the dashboard shows genuine detections and tracks
# on real Indian video. Falls back to the simulator per camera if a clip is
# missing, so the demo never hard-fails on a fresh clone.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REAL_CLIPS = _REPO_ROOT / "data" / "demo" / "india"
_CAM_CLIPS = {
    "CAM-011": "CAM-011.mp4",   # main gate: Indian traffic + pedestrians
    "CAM-014": "CAM-014.mp4",   # perimeter: dense pedestrian flow
    "CAM-022": "CAM-022.mp4",   # open-border approach: mixed traffic
    "CAM-031": "CAM-031.mp4",   # ridge thermal (thermal colormap)
    "CAM-045": "CAM-045.mp4",   # legacy analogue south track (degraded look)
    "CAM-052": "CAM-052.mp4",   # riverine night post (real low-light footage)
}


def _apply_real_sources(cameras: list[Camera]) -> list[Camera]:
    """Point each demo camera at its real clip when the file is present.

    Keeps ``sim_profile`` intact (zone geometry and normalcy seeding derive from
    it), only switching the runtime frame producer to the looping file source.
    """
    for cam in cameras:
        name = _CAM_CLIPS.get(cam.camera_id)
        if not name:
            continue
        clip = _REAL_CLIPS / name
        if clip.exists():
            cam.source_kind = "file"
            cam.stream_url = str(clip)
        else:
            log.warning("real clip missing for %s (%s) — keeping simulator",
                        cam.camera_id, clip)
    return cameras


def demo_cameras(node_id: str) -> list[Camera]:
    """Five cameras that should each end up with a different capability set."""
    return _apply_real_sources([
        Camera(
            camera_id="CAM-011",
            name="Main Gate - Vehicle Lane",
            location="BOP Main Gate, checkpost approach",
            latitude=26.7509, longitude=88.4321,
            source_kind="simulator",
            role=CameraRole.CHOKEPOINT,
            border_profile=BorderProfile.FENCED,
            sensor_type=SensorType.VISIBLE,
            claimed_width=1280, claimed_height=720, claimed_fps=12.0,
            field_of_view_deg=28.0, night_capable=True, estimated_range_m=25.0,
            edge_node=node_id,
            sim_profile={"preset": "gate_hd", "scenario": "chokepoint",
                         "camera_height_m": 3.0, "tilt_deg": 12.0,
                         "fence_distance_m": 12.0},
        ),
        Camera(
            camera_id="CAM-014",
            name="Perimeter North - Fence Line",
            location="Fence line, sector north, post 14",
            latitude=26.7551, longitude=88.4289,
            source_kind="simulator",
            role=CameraRole.PERIMETER,
            border_profile=BorderProfile.FENCED,
            sensor_type=SensorType.VISIBLE,
            claimed_width=1280, claimed_height=720, claimed_fps=12.0,
            field_of_view_deg=62.0, night_capable=False, estimated_range_m=120.0,
            edge_node=node_id,
            sim_profile={"preset": "perimeter_aged", "scenario": "perimeter",
                         "camera_height_m": 6.0, "tilt_deg": 2.0,
                         "fence_distance_m": 45.0},
        ),
        Camera(
            camera_id="CAM-022",
            name="Approach Track - Forest Route",
            location="Unmetalled track, 900 m from checkpost",
            latitude=26.7488, longitude=88.4402,
            source_kind="simulator",
            # The open-border camera. Lawful traffic here is constant, so
            # tripwires are useless and pattern of life carries the weight.
            role=CameraRole.APPROACH,
            border_profile=BorderProfile.OPEN,
            sensor_type=SensorType.IR_ILLUMINATED,
            claimed_width=1280, claimed_height=720, claimed_fps=12.0,
            field_of_view_deg=45.0, night_capable=True, estimated_range_m=90.0,
            edge_node=node_id,
            sim_profile={"preset": "night_ir", "scenario": "approach",
                         "camera_height_m": 5.0, "tilt_deg": 4.0,
                         "fence_distance_m": 40.0},
        ),
        Camera(
            camera_id="CAM-031",
            name="Ridge Thermal",
            location="Observation post, ridge line",
            latitude=26.7602, longitude=88.4198,
            source_kind="simulator",
            role=CameraRole.PERIMETER,
            border_profile=BorderProfile.FENCED,
            sensor_type=SensorType.THERMAL,
            claimed_width=640, claimed_height=480, claimed_fps=9.0,
            field_of_view_deg=50.0, night_capable=True, estimated_range_m=300.0,
            edge_node=node_id,
            sim_profile={"preset": "thermal", "scenario": "perimeter",
                         "camera_height_m": 6.0, "tilt_deg": 2.0,
                         "fence_distance_m": 50.0},
        ),
        Camera(
            camera_id="CAM-045",
            name="Legacy Analogue - South Track",
            location="South approach, encoder-fed legacy feed",
            latitude=26.7431, longitude=88.4355,
            source_kind="simulator",
            role=CameraRole.APPROACH,
            border_profile=BorderProfile.FENCED,
            sensor_type=SensorType.VISIBLE,
            claimed_width=704, claimed_height=480, claimed_fps=12.0,
            field_of_view_deg=70.0, night_capable=False, estimated_range_m=60.0,
            edge_node=node_id,
            sim_profile={"preset": "degraded_legacy", "scenario": "approach",
                         "camera_height_m": 4.5, "tilt_deg": 3.0,
                         "fence_distance_m": 35.0},
        ),
        Camera(
            camera_id="CAM-052",
            name="Riverine Night Post - IR",
            location="River ghat, night observation post",
            latitude=26.7395, longitude=88.4471,
            source_kind="simulator",   # overridden to the real night clip below
            # The night camera. Infiltration happens after dark, so this is the
            # operationally decisive feed: an open-border approach watched under
            # low light where the pattern-of-life doctrine and night movement
            # rules carry the weight.
            role=CameraRole.APPROACH,
            border_profile=BorderProfile.OPEN,
            sensor_type=SensorType.IR_ILLUMINATED,
            claimed_width=1280, claimed_height=720, claimed_fps=12.0,
            field_of_view_deg=50.0, night_capable=True, estimated_range_m=80.0,
            edge_node=node_id,
            sim_profile={"preset": "night_ir", "scenario": "approach",
                         "camera_height_m": 5.0, "tilt_deg": 4.0,
                         "fence_distance_m": 40.0},
        ),
    ])


def _fence_row_normalised(sim: SimulatedCamera) -> float:
    """Where the fence line actually lands in the image, as a 0..1 row."""
    _, v = sim.project(0.0, sim.fence_distance_m)
    return max(0.05, min(0.95, v / sim.height))


def demo_zones(camera: Camera, sim: SimulatedCamera) -> list[Zone]:
    """Zones derived from the camera's own projected geometry."""
    cid = camera.camera_id
    fence_y = _fence_row_normalised(sim)
    zones: list[Zone] = []

    if camera.border_profile is BorderProfile.OPEN:
        # Open border: mark the lawful route. Traffic on it is normal; the rule
        # engine scores what happens away from it.
        zones.append(Zone(
            zone_id=f"{cid}-ROUTE-1",
            camera_id=cid,
            name="Designated Crossing Route",
            kind=ZoneKind.ROUTE,
            is_lawful_route=True,
            points=[
                Point(x=0.40, y=fence_y * 0.80),
                Point(x=0.60, y=fence_y * 0.80),
                Point(x=0.78, y=1.0),
                Point(x=0.22, y=1.0),
            ],
            metadata={"note": "lawful crossing corridor; traffic here is expected"},
        ))
        return zones

    # Fenced doctrine: a tripwire on the fence, and the restricted ground
    # between the fence and the camera.
    zones.append(Zone(
        zone_id=f"{cid}-LINE-1",
        camera_id=cid,
        name="Border Line",
        kind=ZoneKind.LINE,
        points=[Point(x=0.02, y=fence_y), Point(x=0.98, y=fence_y)],
        metadata={"note": "virtual tripwire aligned to the projected fence line"},
    ))

    restricted_top = min(0.97, fence_y + 0.02)
    zones.append(Zone(
        zone_id=f"{cid}-ZONE-1",
        camera_id=cid,
        name="Restricted Zone",
        kind=ZoneKind.POLYGON,
        points=[
            Point(x=0.02, y=restricted_top),
            Point(x=0.98, y=restricted_top),
            Point(x=0.98, y=1.0),
            Point(x=0.02, y=1.0),
        ],
        # Movement toward the camera is inbound from the border; that is the
        # direction of concern, so anything heading the other way is flagged.
        direction_deg=270.0,
        direction_tolerance_deg=75.0,
        metadata={"note": "ground between the fence line and the post"},
    ))
    return zones


def bootstrap_demo_site(db: Database, settings: Settings) -> list[Camera]:
    """Create the demo fleet if the database is empty. Idempotent."""
    existing = db.list_cameras()
    if existing:
        return existing

    log.info("bootstrapping demo site (%d cameras)", len(demo_cameras(settings.node_id)))
    cameras = demo_cameras(settings.node_id)
    normalcy = NormalcyModel(db)

    for camera in cameras:
        db.upsert_camera(camera)

        sim = SimulatedCamera(
            camera_id=camera.camera_id,
            width=camera.claimed_width, height=camera.claimed_height,
            fps=camera.claimed_fps, fov_deg=camera.field_of_view_deg,
            camera_height_m=float(camera.sim_profile.get("camera_height_m", 5.0)),
            tilt_deg=float(camera.sim_profile.get("tilt_deg", 2.0)),
            scenario=camera.sim_profile.get("scenario", "perimeter"),
            fence_distance_m=float(camera.sim_profile.get("fence_distance_m", 45.0)),
        )
        sim.open()   # resolves the fence distance into the visible band

        zones = demo_zones(camera, sim)
        for zone in zones:
            db.upsert_zone(zone)

        # Give the normalcy model a plausible daily rhythm so the demo can show
        # "unusual for this hour" reasoning without waiting a week of wall clock
        # for the camera to learn it. A real deployment learns this itself.
        #
        # Seeded per zone as well as camera-wide, because the scorer compares a
        # zone's hourly count against that same zone's own total. A zone with no
        # baseline at all reads as infinitely unusual, which pinned every event
        # at maximum priority.
        #
        # The demo footage is real Indian street video that is busy at every hour
        # (it is not tied to wall-clock time), so the baseline is seeded busy
        # across all 24 hours. Otherwise the busy footage played at, say, 9pm
        # reads as "unusual for this hour", pins every crossing to CRITICAL and
        # buries the operator — the exact false-alarm flood pattern-of-life is
        # meant to prevent. With a realistic all-hours baseline, a routine
        # crossing is pattern-of-life (rationed by the alert budget) and only a
        # genuine spike stands out.
        # Seeded high because the footage is genuinely dense (hundreds of
        # crossings an hour). The normalcy model compares the recent live rate
        # against expected_recent = bucket/6, so the per-hour baseline must be a
        # few hundred for a busy thoroughfare to read as routine (ratio <= 1.3)
        # rather than as an ongoing anomaly. Then routine crossings take the
        # pattern-of-life downgrade and only genuine spikes stay high.
        allday = range(0, 24)
        for cls in (ObjectClass.PERSON, ObjectClass.CATTLE):
            normalcy.seed_baseline(camera.camera_id, cls, busy_hours=allday,
                                   busy_weight=420.0)
            for zone in zones:
                normalcy.seed_baseline(
                    camera.camera_id, cls, busy_hours=allday,
                    busy_weight=300.0 if zone.is_lawful_route else 220.0,
                    zone_id=zone.zone_id)

    db.audit("system", "demo.bootstrap", settings.node_id,
             {"cameras": [c.camera_id for c in cameras],
              "note": "demo fleet created; simulated sources"})
    return cameras
