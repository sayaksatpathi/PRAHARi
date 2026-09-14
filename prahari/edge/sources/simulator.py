"""Deterministic synthetic border camera.

This is not decoration. Camera capability profiling only means something if
different cameras genuinely deliver different image quality, and a real border
deployment is precisely a mix of a crisp new gate camera and a decade-old fog-
lensed perimeter dome. The simulator produces that spread on purpose:

  * true perspective projection from a ground plane, so a person's pixel height
    is a real function of their distance - which is what makes the DORI
    pixels-on-target calculation and the ground-plane self-calibration testable
    rather than decorative;
  * per-camera optical degradation (defocus blur, sensor noise, block artifacts,
    low-light gain) so the profiler has something real to measure;
  * scripted actors on deterministic tracks, so a demo replays identically.

Everything is seeded. The same seed gives the same scene, every run.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import cv2
import numpy as np

from prahari.common.models import ObjectClass
from prahari.edge.sources.base import Frame, VideoSource

# Physical sizes in metres: (height, width). Used for projection and for the
# pixels-on-target maths downstream.
ACTOR_SIZE: dict[ObjectClass, tuple[float, float]] = {
    ObjectClass.PERSON: (1.70, 0.55),
    ObjectClass.BICYCLE: (1.70, 0.65),
    ObjectClass.MOTORCYCLE: (1.60, 0.80),
    ObjectClass.CAR: (1.55, 1.80),
    ObjectClass.TRUCK: (3.20, 2.50),
    ObjectClass.BUS: (3.20, 2.55),
    ObjectClass.CATTLE: (1.40, 2.00),
    ObjectClass.BOAT: (1.20, 3.00),
    ObjectClass.UAV: (0.35, 0.90),
}

ACTOR_COLOUR: dict[ObjectClass, tuple[int, int, int]] = {
    ObjectClass.PERSON: (48, 52, 78),
    ObjectClass.BICYCLE: (60, 70, 90),
    ObjectClass.MOTORCYCLE: (55, 60, 85),
    ObjectClass.CAR: (90, 80, 70),
    ObjectClass.TRUCK: (70, 75, 80),
    ObjectClass.BUS: (80, 90, 95),
    ObjectClass.CATTLE: (70, 85, 105),
    ObjectClass.BOAT: (85, 80, 70),
    ObjectClass.UAV: (40, 40, 45),
}


@dataclass
class Actor:
    """One moving object on the ground plane, in world metres."""
    actor_id: int
    object_class: ObjectClass
    x: float                 # lateral, metres (0 = optical axis)
    z: float                 # depth, metres from camera
    vx: float                # metres/second
    vz: float
    t_spawn: float = 0.0
    t_despawn: float = 1e9
    altitude: float = 0.0    # metres above ground (UAV only)
    label: str = ""
    plate: str | None = None
    # Set when a scripted demo actor must follow an exact path regardless of
    # elapsed-time jitter.
    scripted: bool = False

    def position_at(self, t: float) -> tuple[float, float]:
        dt = max(0.0, t - self.t_spawn)
        return self.x + self.vx * dt, self.z + self.vz * dt

    def alive_at(self, t: float) -> bool:
        return self.t_spawn <= t <= self.t_despawn


@dataclass
class OpticalProfile:
    """How badly this particular camera mangles the image.

    These are the knobs that make one simulated camera a crisp gate unit and
    another a soft, noisy, over-compressed perimeter dome.
    """
    blur_sigma: float = 0.0          # defocus / lens fog
    noise_sigma: float = 2.0         # sensor noise (rises hard at night)
    block_artifacts: float = 0.0     # 0..1 aggressive recompression
    gain: float = 1.0                # low-light amplification
    brightness: float = 1.0
    vignette: float = 0.15
    is_night: bool = False
    is_thermal: bool = False
    jitter_px: float = 0.0           # pole sway / wind
    dropped_frame_rate: float = 0.0  # flaky PoE or marginal wireless backhaul

    @staticmethod
    def preset(name: str) -> "OpticalProfile":
        presets = {
            # A new gate camera: sharp, well lit, low compression.
            "gate_hd": OpticalProfile(
                blur_sigma=0.4, noise_sigma=1.5, block_artifacts=0.05,
                gain=1.0, brightness=1.05, vignette=0.08),
            # Typical installed perimeter dome, several years old.
            "perimeter_aged": OpticalProfile(
                blur_sigma=1.6, noise_sigma=5.0, block_artifacts=0.35,
                gain=1.1, brightness=0.9, vignette=0.22, jitter_px=0.6),
            # Night, IR illuminated: washed out near field, noisy far field.
            "night_ir": OpticalProfile(
                blur_sigma=1.2, noise_sigma=11.0, block_artifacts=0.30,
                gain=2.2, brightness=0.55, vignette=0.30, is_night=True),
            # Thermal: no colour, low resolution, but works in the dark.
            "thermal": OpticalProfile(
                blur_sigma=0.9, noise_sigma=4.0, block_artifacts=0.10,
                gain=1.0, brightness=0.85, vignette=0.10, is_thermal=True,
                is_night=True),
            # A camera that should fail profiling for anything demanding.
            "degraded_legacy": OpticalProfile(
                blur_sigma=3.2, noise_sigma=9.0, block_artifacts=0.55,
                gain=1.3, brightness=0.75, vignette=0.28, jitter_px=1.4,
                dropped_frame_rate=0.06),
        }
        return presets.get(name, presets["perimeter_aged"])


class SimulatedCamera(VideoSource):
    """A synthetic camera with a real pinhole projection model."""

    def __init__(
        self,
        camera_id: str,
        width: int = 1280,
        height: int = 720,
        fps: float = 12.0,
        fov_deg: float = 60.0,
        camera_height_m: float = 4.0,
        optical: OpticalProfile | None = None,
        scenario: str = "perimeter",
        seed: int = 20260913,
        fence_distance_m: float = 45.0,
        tilt_deg: float = 0.0,
    ) -> None:
        self.camera_id = camera_id
        self.width = int(width)
        self.height = int(height)
        self.nominal_fps = float(fps)
        self.fov_deg = float(fov_deg)
        self.camera_height_m = float(camera_height_m)
        self.tilt_deg = float(tilt_deg)
        self.optical = optical or OpticalProfile.preset("perimeter_aged")
        self.scenario = scenario
        self.fence_distance_m = fence_distance_m

        self._rng = np.random.default_rng(seed)
        self._seed = seed
        self._frame_index = 0
        self._t0 = datetime.now(timezone.utc)
        self._sim_time = 0.0
        self._opened = False
        self._actors: list[Actor] = []
        self._next_actor_id = 1
        self._background: np.ndarray | None = None
        # Tamper simulation (see docs/threat-model.md): an adversary at the
        # border attacks the sensor, not the algorithm.
        self._tamper: str | None = None
        self._frozen_frame: np.ndarray | None = None
        self._vignette_mask: np.ndarray | None = None
        self._noise_bank: dict[float, np.ndarray] | None = None
        self._usable_far_m: float | None = None

        # Pinhole focal length in pixels.
        self.focal_px = (self.width / 2.0) / math.tan(math.radians(self.fov_deg / 2.0))
        self.cx = self.width / 2.0
        self.cy = self.height / 2.0

        # Downward tilt shifts the horizon up the frame (and off it entirely for
        # a steeply tilted camera). Modelling this is not cosmetic: a gate ANPR
        # camera only reaches plate-reading pixel density *because* it is tilted
        # down at close range, and a level-camera-only model can never grant ANPR
        # to anything, which would make the whole capability profile wrong.
        #
        # For moderate tilt this is a first-order model - the horizon moves by
        # f*tan(tilt) and the ground stays linear in 1/Z - which preserves the
        # exact relation the ground-plane self-calibration inverts.
        self.horizon_y = self.cy - self.focal_px * math.tan(math.radians(self.tilt_deg))

    # -- geometry -------------------------------------------------------
    def project(self, x_m: float, z_m: float, y_m: float = 0.0) -> tuple[float, float]:
        """World metres -> pixel coordinates. y_m is height above ground."""
        z = max(0.5, z_m)
        u = self.cx + self.focal_px * (x_m / z)
        v = self.horizon_y + self.focal_px * ((self.camera_height_m - y_m) / z)
        return u, v

    def px_per_metre_at(self, z_m: float) -> float:
        """Pixels per metre of target at depth z. This is the quantity IEC
        62676-4 DORI bands are defined against."""
        return self.focal_px / max(0.5, z_m)

    def depth_at_row(self, v: float) -> float:
        """Invert the projection for a ground point at image row v."""
        denom = (v - self.horizon_y)
        if denom <= 1e-6:
            return float("inf")          # at or above the horizon
        return self.focal_px * self.camera_height_m / denom

    def visible_depth_range(self, target_height_m: float = 1.7) -> tuple[float, float]:
        """Depth window in which a target of this height fits entirely in frame.

        A tilted camera looks at a band of ground, not at everything in front of
        it: too close and the target's feet fall below the bottom edge, too far
        and its head is cut off by the top. Actors have to be placed inside that
        band or the camera simply never sees a whole person - which is exactly
        what happened when the spawn range was a fixed constant, and it silently
        starved the ground-plane calibration of samples.
        """
        # Near limit: the feet must land above the bottom edge.
        denom_near = (self.height - 1) - self.horizon_y
        z_near = (self.focal_px * self.camera_height_m / denom_near) if denom_near > 1e-6 else 3.0

        # Far limit: the head must stay below the top edge. Only binds when the
        # horizon has been tilted off the top of the frame.
        head_h = self.camera_height_m - target_height_m
        if self.horizon_y < 0 and head_h > 0:
            z_far = self.focal_px * head_h / (-self.horizon_y)
        else:
            z_far = 160.0
        return max(3.0, z_near), max(z_near + 2.0, min(z_far, 160.0))

    # -- lifecycle ------------------------------------------------------
    def open(self) -> bool:
        # A fence line has to sit not merely inside the visible band but inside
        # the band where a person is actually *detectable*. A camera whose fence
        # is at 50 m but which falls below the DORI DETECT threshold (25 px/m) at
        # 27 m can never see anyone at or beyond its own fence, so its tripwire
        # is decorative and it reports zero tracks all day. Clamping to the
        # detection limit rather than to mere visibility is the difference
        # between a camera that works and one that only looks like it does.
        z_near, z_far = self.visible_depth_range()
        detect_limit = self.focal_px / 25.0        # DORI DETECT, 25 px per metre
        usable_far = max(z_near + 2.0, min(z_far, detect_limit))
        if not (z_near < self.fence_distance_m < usable_far):
            self.fence_distance_m = round(z_near + (usable_far - z_near) * 0.28, 1)
        self._usable_far_m = usable_far
        self._background = self._render_background()
        self._seed_scenario()
        self._opened = True
        return True

    def close(self) -> None:
        self._opened = False

    @property
    def is_simulated(self) -> bool:
        return True

    # -- scenario -------------------------------------------------------
    def _seed_scenario(self) -> None:
        """Populate the ambient, benign traffic.

        Deliberately includes cattle and routine foot traffic: a system that
        only ever sees intruders in testing will drown its operator in false
        alarms on night one of a real deployment.
        """
        self._actors.clear()
        rng = np.random.default_rng(self._seed)

        if self.scenario == "perimeter":
            ambient = [(ObjectClass.CATTLE, 4), (ObjectClass.PERSON, 5)]
        elif self.scenario == "approach":
            ambient = [(ObjectClass.PERSON, 6), (ObjectClass.MOTORCYCLE, 2),
                       (ObjectClass.CATTLE, 2)]
        elif self.scenario == "chokepoint":
            ambient = [(ObjectClass.CAR, 2), (ObjectClass.TRUCK, 1),
                       (ObjectClass.PERSON, 5)]
        elif self.scenario == "riverine":
            ambient = [(ObjectClass.BOAT, 2), (ObjectClass.PERSON, 4)]
        else:
            ambient = [(ObjectClass.PERSON, 4)]

        for cls, count in ambient:
            for _ in range(count):
                self._spawn_ambient(cls, rng)

    def _spawn_ambient(self, cls: ObjectClass, rng: np.random.Generator) -> None:
        hm, _ = ACTOR_SIZE.get(cls, (1.7, 0.6))
        z_near, z_far = self.visible_depth_range(hm)
        # Cap at the detection limit: actors scattered out to the visible horizon
        # are a handful of pixels tall and populate the scene without populating
        # it for the detector.
        if self._usable_far_m:
            z_far = min(z_far, self._usable_far_m * 1.25)
        lo, hi = z_near * 1.12, max(z_near * 1.3, z_far * 0.90)

        # Sample uniformly in *inverse* depth, not in depth. Apparent size goes
        # as 1/z, so uniform-in-depth sampling crowds almost every actor into the
        # far field where they are a handful of pixels tall - on a wide-angle
        # camera that left the scene populated but effectively empty to the
        # detector, and the ground-plane calibration never collected a sample.
        # Uniform in 1/z gives an even spread of apparent sizes instead.
        inv = rng.uniform(1.0 / hi, 1.0 / lo)
        z = float(1.0 / inv)
        # Keep actors inside the horizontal field of view rather than off frame.
        half_width_m = z * math.tan(math.radians(self.fov_deg / 2.0)) * 0.80
        x = float(rng.uniform(-half_width_m, half_width_m))
        speed = {
            ObjectClass.PERSON: 1.3, ObjectClass.CATTLE: 0.5,
            ObjectClass.MOTORCYCLE: 8.0, ObjectClass.CAR: 9.0,
            ObjectClass.TRUCK: 7.0, ObjectClass.BOAT: 2.5,
        }.get(cls, 1.2)
        heading = float(rng.uniform(0, 2 * math.pi))
        self._actors.append(Actor(
            actor_id=self._next_actor_id,
            object_class=cls,
            x=x, z=z,
            vx=math.cos(heading) * speed * 0.6,
            vz=math.sin(heading) * speed * 0.35,
            t_spawn=float(rng.uniform(0.0, 8.0)),
            t_despawn=1e9,
            label="ambient",
        ))
        self._next_actor_id += 1

    def _recycle_ambient(self, t: float) -> None:
        """Re-seed ambient actors that have wandered out of the visible band.

        Without this the scene drains: background traffic walks out of frame over
        the first couple of minutes and never returns, so a demo left running
        ends up watching an empty stretch of ground, and any camera still trying
        to calibrate its ground plane stalls permanently.
        """
        for actor in self._actors:
            if actor.label != "ambient":
                continue
            x, z = actor.position_at(t)

            # Ambient traffic stays on its own side of the border line. Letting
            # background actors wander freely across it meant the tripwire fired
            # continuously on routine movement, every crossing scored as an
            # intrusion, and the one genuine injected intruder was invisible in
            # the noise. Real foot traffic at a fenced sector does not stroll
            # through the fence, and a simulation in which it does is not
            # testing the rule, it is testing the flood.
            # Only a fenced perimeter keeps traffic strictly to one side. An
            # approach track carries lawful movement on both sides, and confining
            # it to a narrow band beyond the line left the ground-plane fit with
            # no depth leverage at all.
            # A third of perimeter traffic is allowed on the near side of the
            # fence - own patrols walk the line, and a sector where literally
            # nothing ever moves inside the wire is not a real one. It also gives
            # the ground-plane fit the depth spread it needs: confining every
            # observation to a thin band beyond the fence left the regression
            # extrapolating a horizon from samples that barely differed.
            if self.scenario == "perimeter" and (actor.actor_id % 3) != 0 and                     z < self.fence_distance_m * 1.06:
                actor.x, actor.z = x, max(z, self.fence_distance_m * 1.08)
                actor.vz = abs(actor.vz) or 0.4
                actor.t_spawn = t
                continue
            hm, _ = ACTOR_SIZE.get(actor.object_class, (1.7, 0.6))
            z_near, z_far = self.visible_depth_range(hm)
            if self._usable_far_m:
                z_far = min(z_far, self._usable_far_m * 1.25)
            half_width_m = z * math.tan(math.radians(self.fov_deg / 2.0))
            if z_near <= z <= z_far and abs(x) <= half_width_m * 1.2:
                continue

            # A recycled actor is teleported to a new position on the far side
            # of the scene. To any observer - and to the tracker - that is a
            # *different* object arriving, not the same one moving, so it gets a
            # fresh identity. Keeping the old actor_id made the ground truth
            # claim one person had jumped across the frame, which charged the
            # tracker an ID switch it had not made: the simulated IDF1 read
            # 0.018 against 0.288 on real MOT17 footage, entirely as an artefact
            # of this line. Only identity-aware scoring could surface it.
            actor.actor_id = self._next_actor_id
            self._next_actor_id += 1

            inv = self._rng.uniform(1.0 / (z_far * 0.90), 1.0 / (z_near * 1.12))
            new_z = float(1.0 / inv)
            new_half = new_z * math.tan(math.radians(self.fov_deg / 2.0)) * 0.80
            actor.x = float(self._rng.uniform(-new_half, new_half))
            actor.z = new_z
            heading = float(self._rng.uniform(0, 2 * math.pi))
            speed = {
                ObjectClass.PERSON: 1.3, ObjectClass.CATTLE: 0.5,
                ObjectClass.MOTORCYCLE: 8.0, ObjectClass.CAR: 9.0,
                ObjectClass.TRUCK: 7.0, ObjectClass.BOAT: 2.5,
            }.get(actor.object_class, 1.2)
            actor.vx = math.cos(heading) * speed * 0.6
            actor.vz = math.sin(heading) * speed * 0.35
            actor.t_spawn = t

    # -- demo injection -------------------------------------------------
    def inject_intruder(self, *, from_x: float = -14.0, speed: float = 1.5,
                        approach_seconds: float = 0.0) -> int:
        """Walk a person from outside the fence line, across it, inbound.

        This is the scripted intrusion the demo triggers. It starts beyond the
        virtual line and walks toward the camera so it genuinely crosses.
        """
        _, z_far = self.visible_depth_range()
        start_z = min(self.fence_distance_m + 22.0, z_far * 0.95)

        # Lateral start derived from the field of view, not a fixed -14 m. On a
        # narrow gate camera (28 degrees) the visible half-width at ~30 m is only
        # a few metres, so a hardcoded offset put the intruder off-frame - the
        # subject was never seen and the scripted incident silently produced no
        # event. The intruder starts within the frame and converges on the
        # optical axis as it approaches, so it stays visible all the way in.
        half_width_m = start_z * math.tan(math.radians(self.fov_deg / 2.0))
        start_x = max(-abs(from_x), -half_width_m * 0.55)
        travel_time = max(1.0, (start_z - max(self.fence_distance_m * 0.5, 4.0)) / speed)

        actor = Actor(
            actor_id=self._next_actor_id,
            object_class=ObjectClass.PERSON,
            x=start_x,
            z=start_z,
            vx=-start_x / travel_time,
            vz=-speed,
            t_spawn=self._sim_time + approach_seconds,
            t_despawn=self._sim_time + approach_seconds + 90.0,
            label="intruder",
            scripted=True,
        )
        self._actors.append(actor)
        self._next_actor_id += 1
        return actor.actor_id

    def inject_vehicle(self, plate: str | None = None,
                       cls: ObjectClass = ObjectClass.TRUCK) -> int:
        z_near, z_far = self.visible_depth_range(ACTOR_SIZE.get(cls, (3.2, 2.5))[0])
        # A vehicle taller than the camera has no far limit from the head-room
        # test, so z_far comes back as the clamp and the vehicle would be spawned
        # at ~150 m travelling at 17 m/s - past the camera before its plate is
        # ever large enough to read. Hold it inside the usable band and let it
        # approach at a road-plausible speed.
        if self._usable_far_m:
            z_far = min(z_far, self._usable_far_m)
        start_z = z_far * 0.92
        approach = max(2.0, min(9.0, (z_far - z_near) / 9.0))

        # Lateral offset has to be derived from the field of view, not fixed. A
        # hardcoded 6 m puts the vehicle outside a 28-degree gate camera's frame
        # entirely - the visible half-width at 17 m is only about 4 m - so it was
        # spawned off-frame, never rendered, and ANPR had nothing to read while
        # appearing to be correctly configured.
        half_width_m = start_z * math.tan(math.radians(self.fov_deg / 2.0))
        start_x = half_width_m * 0.30

        # Converge on the optical axis over the approach, so the vehicle stays in
        # frame all the way in rather than drifting out of the side.
        travel_time = max(1.0, (start_z - max(z_near, 4.0)) / approach)

        actor = Actor(
            actor_id=self._next_actor_id,
            object_class=cls,
            x=start_x, z=start_z,
            vx=-start_x / travel_time,
            vz=-approach,
            t_spawn=self._sim_time,
            t_despawn=self._sim_time + 60.0,
            label="demo_vehicle",
            plate=plate,
            scripted=True,
        )
        self._actors.append(actor)
        self._next_actor_id += 1
        return actor.actor_id

    def inject_patrol(self, *, outbound: bool = False, speed: float = 1.3,
                      size: int = 2, running: bool = False) -> list[int]:
        """Walk a foot patrol along the fence ground, inbound or outbound.

        Separate from `inject_intruder` because the patrol demonstration needs
        the one thing an intruder injection does not offer: control of the
        direction of travel. A patrol walking its route and a patrol walking it
        backwards must be the same injection with one flag changed, or the
        demonstration is not showing what it claims to.
        """
        z_near, z_far = self.visible_depth_range()
        pace = speed * (3.2 if running else 1.0)
        if outbound:
            start_z = max(z_near, min(self.fence_distance_m * 0.55, z_far * 0.35))
            vz = +pace
        else:
            start_z = min(self.fence_distance_m + 18.0, z_far * 0.92)
            vz = -pace

        half_width_m = start_z * math.tan(math.radians(self.fov_deg / 2.0))
        ids: list[int] = []
        for i in range(max(1, size)):
            lateral = -half_width_m * 0.40 + i * min(1.6, half_width_m * 0.18)
            actor = Actor(
                actor_id=self._next_actor_id,
                object_class=ObjectClass.PERSON,
                x=lateral, z=start_z + i * 1.5,
                # Converge gently on the axis so the patrol stays in frame for
                # the whole transit rather than drifting out of the side.
                vx=-lateral / max(1.0, abs((start_z - z_near) / max(pace, 0.1))),
                vz=vz,
                t_spawn=self._sim_time + i * 0.6,
                t_despawn=self._sim_time + 90.0,
                label="patrol",
                scripted=True,
            )
            self._actors.append(actor)
            ids.append(actor.actor_id)
            self._next_actor_id += 1
        return ids

    def inject_loiterer(self, dwell_seconds: float = 90.0) -> int:
        actor = Actor(
            actor_id=self._next_actor_id,
            object_class=ObjectClass.PERSON,
            x=-3.0, z=self.fence_distance_m * 0.75,
            vx=0.05, vz=0.02,          # essentially stationary: that is the point
            t_spawn=self._sim_time,
            t_despawn=self._sim_time + dwell_seconds,
            label="loiterer",
            scripted=True,
        )
        self._actors.append(actor)
        self._next_actor_id += 1
        return actor.actor_id

    def inject_group(self, size: int = 4) -> list[int]:
        ids = []
        for i in range(size):
            actor = Actor(
                actor_id=self._next_actor_id,
                object_class=ObjectClass.PERSON,
                x=-10.0 + i * 1.4,
                z=min(self.fence_distance_m + 16.0 + (i % 2) * 2.0,
                      self.visible_depth_range()[1] * 0.94),
                vx=0.2, vz=-1.4,
                t_spawn=self._sim_time + i * 0.7,
                t_despawn=self._sim_time + 90.0,
                label="group",
                scripted=True,
            )
            self._actors.append(actor)
            ids.append(actor.actor_id)
            self._next_actor_id += 1
        return ids

    def set_tamper(self, mode: str | None) -> None:
        """mode: None | 'blinded' | 'covered' | 'rotated' | 'frozen'."""
        self._tamper = mode
        if mode != "frozen":
            self._frozen_frame = None

    # -- rendering ------------------------------------------------------
    def _render_background(self) -> np.ndarray:
        h, w = self.height, self.width
        img = np.zeros((h, w, 3), dtype=np.uint8)
        # A steeply tilted camera sees no sky at all; clamp rather than special-case.
        horizon = int(np.clip(self.horizon_y, 0, h - 2))

        # Sky: a vertical gradient. At night it is nearly black.
        sky_top = np.array([70, 58, 44], dtype=np.float32)
        sky_bot = np.array([132, 120, 104], dtype=np.float32)
        if self.optical.is_night:
            sky_top *= 0.18
            sky_bot *= 0.25
        for y in range(horizon):
            t = y / max(1, horizon)
            img[y, :] = (sky_top * (1 - t) + sky_bot * t).astype(np.uint8)

        # Ground: darker with depth compression toward the horizon.
        g_near = np.array([58, 74, 66], dtype=np.float32)
        g_far = np.array([96, 104, 92], dtype=np.float32)
        if self.optical.is_night:
            g_near *= 0.30
            g_far *= 0.22
        for y in range(horizon, h):
            t = (y - horizon) / max(1, h - horizon)
            img[y, :] = (g_far * (1 - t) + g_near * t).astype(np.uint8)

        # Terrain texture, deterministic so profiling measurements are stable.
        rng = np.random.default_rng(self._seed ^ 0x5EED)
        noise = rng.normal(0, 6.0, (h, w, 1)).astype(np.float32)
        img = np.clip(img.astype(np.float32) + noise, 0, 255).astype(np.uint8)

        # Scrub: small dark blobs, denser in the near field.
        for _ in range(220):
            z = float(rng.uniform(12.0, 160.0))
            x = float(rng.uniform(-z * 0.7, z * 0.7))
            u, v = self.project(x, z)
            if 0 <= int(v) < h and 0 <= int(u) < w:
                r = max(1, int(self.px_per_metre_at(z) * 0.35))
                cv2.circle(img, (int(u), int(v)), r, (46, 60, 52), -1, cv2.LINE_AA)

        self._draw_fixed_features(img)
        return img

    def _draw_fixed_features(self, img: np.ndarray) -> None:
        """Fence line / track / river, depending on the camera's scenario."""
        if self.scenario == "riverine":
            z_near, z_far = 25.0, 120.0
            pts = []
            for z in np.linspace(z_near, z_far, 40):
                u, v = self.project(-z * 0.35, float(z))
                pts.append([u, v])
            for z in np.linspace(z_far, z_near, 40):
                u, v = self.project(z * 0.20, float(z))
                pts.append([u, v])
            poly = np.array([pts], dtype=np.int32)
            colour = (90, 70, 50) if not self.optical.is_night else (30, 24, 18)
            cv2.fillPoly(img, poly, colour, cv2.LINE_AA)
            return

        # Fence / border line running laterally across the field of view.
        z = self.fence_distance_m
        post_colour = (120, 125, 130) if not self.optical.is_night else (45, 47, 50)
        u_l, v_l = self.project(-60.0, z)
        u_r, v_r = self.project(60.0, z)
        cv2.line(img, (int(u_l), int(v_l)), (int(u_r), int(v_r)), post_colour, 2, cv2.LINE_AA)
        # Posts, correctly foreshortened.
        for x in np.arange(-40.0, 40.0, 4.0):
            ub, vb = self.project(float(x), z, 0.0)
            ut, vt = self.project(float(x), z, 2.2)
            if 0 <= ub < self.width:
                cv2.line(img, (int(ub), int(vb)), (int(ut), int(vt)),
                         post_colour, max(1, int(self.px_per_metre_at(z) * 0.06)),
                         cv2.LINE_AA)

        if self.scenario in ("chokepoint", "approach"):
            # A track leading away from the camera.
            pts = []
            for zz in np.linspace(8.0, 140.0, 40):
                u, v = self.project(-1.8, float(zz))
                pts.append([u, v])
            for zz in np.linspace(140.0, 8.0, 40):
                u, v = self.project(2.2, float(zz))
                pts.append([u, v])
            road = (74, 78, 82) if not self.optical.is_night else (26, 27, 29)
            cv2.fillPoly(img, np.array([pts], dtype=np.int32), road, cv2.LINE_AA)

    def _draw_actor(self, img: np.ndarray, actor: Actor, t: float) -> dict[str, Any] | None:
        x, z = actor.position_at(t)
        if z < 4.0 or z > 200.0:
            return None
        hm, wm = ACTOR_SIZE.get(actor.object_class, (1.7, 0.6))
        u_c, v_base = self.project(x, z, actor.altitude)
        _, v_top = self.project(x, z, actor.altitude + hm)
        px_h = abs(v_base - v_top)
        px_w = self.px_per_metre_at(z) * wm
        if px_h < 2.0 or px_w < 1.0:
            return None

        x1, x2 = u_c - px_w / 2.0, u_c + px_w / 2.0
        y1, y2 = v_top, v_base
        if x2 < 0 or x1 > self.width or y2 < 0 or y1 > self.height:
            return None

        colour = ACTOR_COLOUR.get(actor.object_class, (60, 60, 60))
        if self.optical.is_thermal:
            # Warm bodies are bright in thermal; vehicles hotter still.
            warmth = 235 if actor.object_class in (
                ObjectClass.PERSON, ObjectClass.CATTLE) else 255
            colour = (warmth, warmth, warmth)
        elif self.optical.is_night:
            colour = tuple(int(c * 0.45) for c in colour)

        ix1, iy1 = int(round(x1)), int(round(y1))
        ix2, iy2 = int(round(x2)), int(round(y2))

        if actor.object_class in (ObjectClass.PERSON, ObjectClass.CATTLE):
            # Torso + head so the silhouette is not a bare rectangle; detectors
            # and the sharpness measurement both benefit from real edges.
            head_r = max(1, int(px_h * 0.12))
            cv2.rectangle(img, (ix1, iy1 + head_r), (ix2, iy2), colour, -1, cv2.LINE_AA)
            cv2.circle(img, (int(u_c), iy1 + head_r), head_r, colour, -1, cv2.LINE_AA)
            if px_h > 16 and actor.object_class is ObjectClass.PERSON:
                mid = int((ix1 + ix2) / 2)
                cv2.line(img, (mid, int(iy1 + px_h * 0.55)), (mid, iy2),
                         (max(0, colour[0] - 18), max(0, colour[1] - 18),
                          max(0, colour[2] - 18)), 1, cv2.LINE_AA)
        else:
            cv2.rectangle(img, (ix1, iy1), (ix2, iy2), colour, -1, cv2.LINE_AA)
            if actor.object_class.is_vehicle and px_h > 14:
                # Windscreen band gives vehicles a distinguishable structure.
                cv2.rectangle(img, (ix1 + 2, iy1 + 2), (ix2 - 2, iy1 + int(px_h * 0.38)),
                              (min(255, colour[0] + 35), min(255, colour[1] + 35),
                               min(255, colour[2] + 35)), -1, cv2.LINE_AA)

        # Number plate: only rendered when it is physically large enough to be
        # legible. The ANPR module must earn its reads, not be handed them.
        plate_box = None
        if actor.plate and actor.object_class.is_vehicle:
            plate_w_px = self.px_per_metre_at(z) * 0.50      # ~500 mm plate
            plate_h_px = self.px_per_metre_at(z) * 0.11
            if plate_w_px >= 18:
                pu1 = int(u_c - plate_w_px / 2)
                pu2 = int(u_c + plate_w_px / 2)
                pv2 = int(v_base - px_h * 0.12)
                pv1 = int(pv2 - plate_h_px)
                cv2.rectangle(img, (pu1, pv1), (pu2, pv2), (235, 235, 235), -1)
                if plate_w_px >= 46:
                    scale = plate_w_px / 150.0
                    cv2.putText(img, actor.plate, (pu1 + 2, pv2 - 2),
                                cv2.FONT_HERSHEY_SIMPLEX, scale, (15, 15, 15),
                                max(1, int(scale * 2)), cv2.LINE_AA)
                plate_box = [pu1, pv1, pu2, pv2]

        return {
            "actor_id": actor.actor_id,
            "object_class": actor.object_class.value,
            "bbox": [max(0.0, x1), max(0.0, y1),
                     min(float(self.width), x2), min(float(self.height), y2)],
            "distance_m": round(z, 2),
            "px_per_metre": round(self.px_per_metre_at(z), 2),
            "px_height": round(px_h, 1),
            "label": actor.label,
            "plate": actor.plate,
            "plate_bbox": plate_box,
            "world": [round(x, 2), round(z, 2)],
        }

    def _noise_slice(self, sigma: float) -> np.ndarray:
        """Sensor noise as a zero-copy view into a pre-generated int16 field.

        Drawing a fresh full-frame Gaussian costs ~2.8 million samples per camera
        per frame and dominated the entire pipeline. Instead a double-height
        unit-noise field is generated once per sigma, and each frame takes a
        random row offset into it - which is a view, not a copy. Frame-to-frame
        the noise is uncorrelated enough that the profiler's noise-floor estimate
        is unchanged, at roughly a twentieth of the cost.
        """
        key = round(sigma, 1)
        bank = self._noise_bank or {}
        field = bank.get(key)
        if field is None:
            rng = np.random.default_rng(self._seed ^ 0x9E3 ^ int(key * 10))
            field = rng.normal(0.0, sigma, (self.height * 2, self.width, 3))
            field = np.clip(field, -127, 127).astype(np.int16)
            bank[key] = field
            self._noise_bank = bank
        offset = int(self._rng.integers(0, self.height))
        return field[offset:offset + self.height]

    def _vignette(self) -> np.ndarray:
        """Cached radial falloff. Rebuilding an np.mgrid the size of the frame
        every frame cost more than the detector did."""
        if self._vignette_mask is None:
            ys, xs = np.mgrid[0:self.height, 0:self.width]
            r = np.sqrt(((xs - self.cx) / self.cx) ** 2
                        + ((ys - self.cy) / self.cy) ** 2)
            mask = 1.0 - self.optical.vignette * np.clip(r, 0, 1.6)
            self._vignette_mask = np.repeat(
                mask.astype(np.float32)[:, :, None], 3, axis=2)
        return self._vignette_mask

    def _apply_optics(self, img: np.ndarray) -> np.ndarray:
        """Optical degradation chain, kept in uint8 throughout.

        Every step here uses an OpenCV primitive on 8-bit data rather than the
        numpy float equivalent. That is not micro-optimisation: converting a 720p
        frame to float32 and blurring it there cost ~22 ms against ~1.5 ms for
        the same blur in uint8, and across five cameras that difference was the
        whole frame-rate budget.
        """
        o = self.optical
        out = img

        if o.is_thermal:
            grey = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
            out = cv2.cvtColor(grey, cv2.COLOR_GRAY2BGR)

        scale = o.brightness * o.gain
        if abs(scale - 1.0) > 1e-3:
            out = cv2.convertScaleAbs(out, alpha=scale)

        if o.blur_sigma > 0.05:
            k = int(max(3, round(o.blur_sigma * 4) | 1))
            out = cv2.GaussianBlur(out, (k, k), o.blur_sigma)

        if o.vignette > 0.01:
            out = cv2.multiply(out, self._vignette(), dtype=cv2.CV_8U)

        if o.noise_sigma > 0.01:
            out = cv2.add(out, self._noise_slice(o.noise_sigma), dtype=cv2.CV_8U)

        if o.block_artifacts > 0.01:
            # Genuine recompression, not a blockiness filter that imitates one,
            # so the artifact score the profiler computes measures a real thing.
            quality = int(np.clip(92 - o.block_artifacts * 70, 8, 95))
            ok, enc = cv2.imencode(".jpg", out, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if ok:
                out = cv2.imdecode(enc, cv2.IMREAD_COLOR)
        return out

    def _apply_tamper(self, img: np.ndarray) -> np.ndarray:
        if self._tamper == "covered":
            return np.full_like(img, 18)
        if self._tamper == "blinded":
            glare = np.full_like(img, 250)
            return cv2.addWeighted(img, 0.15, glare, 0.85, 0)
        if self._tamper == "rotated":
            m = cv2.getRotationMatrix2D((self.cx, self.cy), 37.0, 1.0)
            return cv2.warpAffine(img, m, (self.width, self.height))
        if self._tamper == "frozen":
            # Stream replay: the same frame forever. Detectable by frame hashing.
            if self._frozen_frame is None:
                self._frozen_frame = img.copy()
            return self._frozen_frame.copy()
        return img

    # -- frame production -----------------------------------------------
    def read(self) -> Frame | None:
        if not self._opened or self._background is None:
            return None

        # Dropped frames: a marginal PoE run or a saturated wireless backhaul.
        if self.optical.dropped_frame_rate > 0 and \
                self._rng.random() < self.optical.dropped_frame_rate:
            self._frame_index += 1
            self._sim_time += 1.0 / self.nominal_fps
            return None

        t = self._sim_time
        self._recycle_ambient(t)
        img = self._background.copy()

        ground_truth: list[dict[str, Any]] = []
        # Painter's algorithm: far objects first so near ones occlude them.
        alive = [a for a in self._actors if a.alive_at(t)]
        alive.sort(key=lambda a: a.position_at(t)[1], reverse=True)
        for actor in alive:
            gt = self._draw_actor(img, actor, t)
            if gt:
                ground_truth.append(gt)

        if self.optical.jitter_px > 0.01:
            dx = float(self._rng.normal(0, self.optical.jitter_px))
            dy = float(self._rng.normal(0, self.optical.jitter_px))
            m = np.float32([[1, 0, dx], [0, 1, dy]])
            img = cv2.warpAffine(img, m, (self.width, self.height),
                                 borderMode=cv2.BORDER_REPLICATE)

        img = self._apply_optics(img)
        img = self._apply_tamper(img)

        frame = Frame(
            image=img,
            index=self._frame_index,
            timestamp=self._t0 + timedelta(seconds=self._sim_time),
            ground_truth=ground_truth if self._tamper is None else [],
        )
        self._frame_index += 1
        self._sim_time += 1.0 / self.nominal_fps
        return frame

    # -- introspection --------------------------------------------------
    @property
    def sim_time(self) -> float:
        return self._sim_time

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update({
            "scenario": self.scenario,
            "fov_deg": self.fov_deg,
            "tilt_deg": self.tilt_deg,
            "camera_height_m": self.camera_height_m,
            "horizon_y": round(self.horizon_y, 1),
            "focal_px": round(self.focal_px, 1),
            "fence_distance_m": self.fence_distance_m,
            "night": self.optical.is_night,
            "thermal": self.optical.is_thermal,
            "tamper": self._tamper,
            "actors": len(self._actors),
        })
        return d
