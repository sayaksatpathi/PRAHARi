"""Per-camera processing pipeline.

One instance per camera, running the full path:

    frame -> profiling -> detection (capability-gated) -> tracking
          -> rules -> scoring -> evidence -> ledger -> database -> bus

Three design decisions worth stating.

**Profiling runs first and gates everything after it.** A camera begins in a
PROFILING state and runs no analytics beyond person detection, which it needs in
order to self-calibrate its ground plane. Only once it has a capability
certificate does the rest of its analytics set switch on, and only the analytics
the certificate actually grants. This is the difference between a system that
claims to be camera-aware and one that is: the gate is in the execution path,
not in the brochure.

**Inference is decoupled from frame rate.** Detection runs every Nth frame while
tracking runs on every frame, so the tracker coasts between detections. This is
what makes a dozen cameras viable on one edge box; running a detector on every
frame of every camera is how these deployments end up needing hardware nobody
budgeted for.

**Blocking work stays off the event loop.** Decode and inference run in a thread
executor. A stalled camera - which at a border means a camera on a wet PoE run,
so, routinely - must never be able to freeze the dashboard or the sync queue.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import cv2
import numpy as np

from prahari.common.bus import MessageBus, subj
from prahari.common.config import Settings
from prahari.common.db import Database
from prahari.common.models import (
    Camera,
    CameraHealth,
    Capability,
    CapabilityCertificate,
    Event,
    EventType,
    EvidenceRef,
    ObjectClass,
    Priority,
    Track,
    Zone,
)
from prahari.edge.alerting import AlertGovernor
from prahari.edge.detect.base import Detector
from prahari.edge.evidence import EvidenceBuffer, EvidenceLedger, EvidenceStore, PendingClip
from prahari.edge.normalcy import NormalcyModel
from prahari.edge.priority import ScoringContext, score_event
from prahari.edge.profiling.certificate import issue_certificate
from prahari.edge.profiling.measure import CameraProfiler
from prahari.edge.rules.engine import EventCandidate, RuleContext, RuleEngine
from prahari.edge.sources.base import VideoSource
from prahari.edge.tamper import TamperDetector
from prahari.edge.track.bytetrack import ByteTracker

log = logging.getLogger("prahari.pipeline")

# Colours are keyed to how much attention the object deserves, not to class
# identity, so an operator scanning a wall of cameras reads urgency first.
BOX_COLOUR = {
    ObjectClass.PERSON: (64, 196, 255),
    ObjectClass.CATTLE: (140, 140, 140),
    ObjectClass.UAV: (80, 80, 255),
}
DEFAULT_BOX_COLOUR = (120, 220, 160)

# Upper bound on simultaneous clip captures per camera. Each holds a full
# JPEG pre-roll in memory, so this is a memory ceiling, not a nicety.
MAX_PENDING_CLIPS = 12

# How long to wait before re-attempting calibration on a camera that was
# certified without a ground plane. Traffic is not uniform: a camera that saw
# nobody during a quiet night may see plenty at first light, and a camera that
# gave up once should not stay geometry-blind for the rest of its deployment.
RECALIBRATION_RETRY_FRAMES = 2400


class CameraPipeline:
    """Owns one camera end to end."""

    def __init__(
        self,
        camera: Camera,
        source: VideoSource,
        detector: Detector,
        db: Database,
        bus: MessageBus,
        settings: Settings,
        evidence_store: EvidenceStore,
        ledger: EvidenceLedger,
        normalcy: NormalcyModel,
        meter=None,
        governor: AlertGovernor | None = None,
    ) -> None:
        self.camera = camera
        self.source = source
        self.detector = detector
        self.db = db
        self.bus = bus
        self.settings = settings
        self.evidence_store = evidence_store
        self.ledger = ledger
        self.normalcy = normalcy
        self.meter = meter
        self.governor = governor

        self.tracker = ByteTracker(
            camera.camera_id,
            match_iou=settings.track_match_iou,
            max_lost_frames=int(settings.track_timeout_seconds * source.nominal_fps),
        )
        self.rules = RuleEngine(camera.camera_id)
        self.profiler = CameraProfiler(
            camera.camera_id, min_frames=40, min_ground_samples=28,
            max_frames=int(source.nominal_fps * 240),  # generous: cold start is the worst case
        )
        self.tamper = TamperDetector()
        self.buffer = EvidenceBuffer(
            source.nominal_fps,
            settings.event_clip_before_seconds,
            settings.event_clip_after_seconds,
        )

        self.certificate: CapabilityCertificate | None = None
        self.state = "profiling"
        self.zones: list[Zone] = []

        self._frame_index = 0
        self._processed = 0
        self._detections: list = []
        self._tracks: list[Track] = []
        self._latest_jpeg: bytes | None = None
        self._pending_clips: list[PendingClip] = []
        self._counted_tracks: set[int] = set()
        self._running = False
        self._task: asyncio.Task | None = None
        self._fps_window: list[float] = []
        self._last_frame_at = 0.0
        self._consecutive_failures = 0
        self._tamper_fired = False
        self._recalibrate_at: int | None = None

        # Profiling needs person detections before anything else can be granted,
        # so the first pass runs with exactly that and nothing more.
        self._profiling_caps = {Capability.PERSON_DETECTION, Capability.TRACKING}

    # -- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        if not self.source.open():
            self.camera.health = CameraHealth.OFFLINE
            log.error("camera %s failed to open", self.camera.camera_id)
            return
        self.detector.warmup()
        self.zones = self.db.list_zones(self.camera.camera_id)
        self.certificate = self.db.latest_certificate(self.camera.camera_id)
        # Resume as "running" only if the stored certificate actually has
        # geometry. Restoring a geometry-less certificate straight into the
        # running state would re-create the deadlock on every restart.
        if self.certificate and self.certificate.measurement.ground_plane_estimated:
            self.state = "running"
        self.camera.health = CameraHealth.ONLINE
        self._running = True
        self._task = asyncio.create_task(
            self._loop(), name=f"prahari-cam-{self.camera.camera_id}"
        )

    async def stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.source.close()

    def reload_zones(self) -> None:
        self.zones = self.db.list_zones(self.camera.camera_id)

    async def _loop(self) -> None:
        interval = 1.0 / max(1.0, self.source.nominal_fps)
        loop = asyncio.get_running_loop()
        while self._running:
            started = time.monotonic()
            try:
                candidates = await loop.run_in_executor(None, self._process_frame)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("pipeline error on %s", self.camera.camera_id)
                candidates = []

            for cand in candidates:
                await self._emit_event(cand)

            await self._publish_live()

            elapsed = time.monotonic() - started
            await asyncio.sleep(max(0.0, interval - elapsed))

    # -- frame processing (runs in executor) -----------------------------
    def _process_frame(self) -> list[EventCandidate]:
        frame = self.source.read()
        if frame is None:
            self._consecutive_failures += 1
            if self._consecutive_failures > self.source.nominal_fps * 5:
                self.camera.health = CameraHealth.OFFLINE
            return []
        self._consecutive_failures = 0
        self._frame_index = frame.index
        ts = frame.timestamp.timestamp()

        now_mono = time.monotonic()
        if self._last_frame_at:
            self._fps_window.append(now_mono - self._last_frame_at)
            if len(self._fps_window) > 60:
                self._fps_window.pop(0)
        self._last_frame_at = now_mono

        # --- integrity before analytics ---------------------------------
        verdict = self.tamper.update(frame.image)
        if verdict.tampered and not self._tamper_fired:
            self._tamper_fired = True
            self.camera.health = CameraHealth.TAMPERED
            return [EventCandidate(
                event_type=(EventType.STREAM_REPLAY_SUSPECTED if verdict.kind == "frozen"
                            else EventType.CAMERA_TAMPER),
                track=None, zone=None,
                summary=f"Camera integrity alert ({verdict.kind}): {verdict.detail}",
                dedupe_key=f"{self.camera.camera_id}:tamper:{verdict.kind}",
                detail={"kind": verdict.kind, "confidence": verdict.confidence},
            )]
        if not verdict.tampered and self._tamper_fired:
            self._tamper_fired = False
            self.camera.health = CameraHealth.ONLINE

        self.buffer.push(frame.image, ts)
        latest = self.buffer.latest()
        if latest and self.meter:
            self.meter.observe_frame(len(latest[1]))
        self._advance_pending_clips(latest)

        # --- profiling ---------------------------------------------------
        if self.state == "profiling":
            self.profiler.observe_frame(frame.image, ts)

        # --- detection, gated by the certificate -------------------------
        # A profiling camera must always be permitted person detection, even if
        # an earlier certificate exists and grants nothing. Without this the
        # system deadlocks: a camera that failed to fit its ground plane is
        # granted no analytics, therefore detects nobody, therefore never
        # accumulates the pedestrian observations it needs to fit the ground
        # plane, and stays blind permanently.
        if self.state == "profiling":
            allowed = set(self._profiling_caps)
            if self.certificate:
                allowed |= self.certificate.granted()
        else:
            allowed = (self.certificate.granted() if self.certificate
                       else set(self._profiling_caps))
        run_detector = (self._frame_index % max(1, self.settings.inference_interval)) == 0
        if run_detector:
            context: dict[str, Any] = {"ground_truth": frame.ground_truth}
            if self.certificate:
                m = self.certificate.measurement
                context.update({
                    "effective_resolution_factor": m.effective_resolution_factor,
                    "noise_sigma": m.noise_sigma,
                    "compression_artifact_score": m.compression_artifact_score,
                    "is_low_light": m.is_low_light,
                })
            self._detections = self.detector.infer(
                frame.image, allowed=allowed,
                frame_index=self._frame_index, context=context,
            )

        self._tracks = self.tracker.update(
            self._detections if run_detector else [], frame.timestamp, ts
        )

        # Feed the ground-plane fit. Edge-clipped boxes are rejected: their
        # apparent height is wrong and would corrupt the calibration.
        if self.state == "profiling":
            h, w = frame.image.shape[:2]
            for t in self._tracks:
                if t.object_class is not ObjectClass.PERSON:
                    continue
                # Calibrate on observations, never on predictions. A track the
                # detector missed this frame is being coasted forward on a
                # constant-velocity estimate, and its box is where the tracker
                # *thinks* the person is. Feeding those into a fit that reads box
                # height as a distance measurement injects the tracker's own
                # drift into the camera's geometry.
                if t.lost_frames > 0 or t.hits < 5:
                    continue
                b = t.bbox
                if b.y2 >= h - 2 or b.y1 <= 1 or b.x1 <= 1 or b.x2 >= w - 2:
                    continue
                self.profiler.observe_person(foot_v=b.y2, px_height=b.height,
                                            px_width=b.width)
            if self.profiler.ready:
                self._issue_certificate()

        self._learn_normalcy(frame.timestamp)
        if self._frame_index % 150 == 0:
            self._sweep_unchained()
        self._render(frame.image)

        if self.state != "running":
            return []

        ctx = RuleContext(
            camera=self.camera,
            zones=self.zones,
            certificate=self.certificate,
            now=frame.timestamp,
            timestamp_s=ts,
            frame_width=frame.image.shape[1],
            frame_height=frame.image.shape[0],
            is_night=bool(self.certificate and self.certificate.measurement.is_low_light),
            loitering_threshold_s=self.settings.loitering_threshold_seconds,
            group_size_threshold=self.settings.group_size_threshold,
            group_window_s=self.settings.group_window_seconds,
            cooldown_s=self.settings.event_cooldown_seconds,
        )
        return self.rules.evaluate(self.tracker, self._tracks, ctx)

    def _issue_certificate(self) -> None:
        measurement = self.profiler.build(claimed_fps=self.camera.claimed_fps)
        version = self.db.next_certificate_version(self.camera.camera_id)
        cert = issue_certificate(self.camera, measurement, version=version)
        self.db.save_certificate(cert)
        self.certificate = cert
        self.camera.certificate_id = cert.certificate_id
        self.db.upsert_camera(self.camera)

        if measurement.ground_plane_estimated:
            self.state = "running"
            self._recalibrate_at = None
        else:
            # The certificate is still recorded - it documents what was measured
            # and states plainly why geometry could not be fitted - but the
            # camera stays in profiling and keeps trying. Declaring it "running"
            # with an empty grant list would present a blind camera as a working
            # one.
            log.warning(
                "camera %s could not fit a ground plane (%d person observations); "
                "certificate recorded, remaining in profiling and retrying",
                self.camera.camera_id, len(self.profiler.acc.ground),
            )
            self._restart_profiling()

        granted = sorted(c.value for c in cert.granted())
        log.info(
            "camera %s profiled: DORI=%s, granted=%s",
            self.camera.camera_id, cert.overall_dori.value, granted,
        )
        self.db.audit("system", "certificate.issued", self.camera.camera_id,
                      {"version": version, "granted": granted,
                       "digest": cert.digest[:16]})

    def _restart_profiling(self) -> None:
        """Re-enter profiling without discarding the existing certificate.

        The camera keeps operating under whatever it was granted while it
        re-measures, so a retry costs no coverage.
        """
        self._recalibrate_at = None
        self.profiler = CameraProfiler(
            self.camera.camera_id, min_frames=40, min_ground_samples=28,
            max_frames=int(self.source.nominal_fps * 240),
        )
        self.state = "profiling"
        log.info("camera %s re-attempting ground-plane calibration",
                 self.camera.camera_id)

    def _learn_normalcy(self, when: datetime) -> None:
        """Count each track once, not once per frame.

        Counting per frame would teach the model that a person standing still is
        enormous traffic, and the next genuine event in that bucket would score
        as routine.
        """
        for t in self._tracks:
            if t.track_id in self._counted_tracks or t.hits < 5:
                continue
            self._counted_tracks.add(t.track_id)
            self.normalcy.observe(self.camera.camera_id, t.object_class, when)
            # And once per zone it is standing in, so each zone builds its own
            # baseline rather than inheriting the camera's.
            for zone_id in t.zones:
                self.normalcy.observe(self.camera.camera_id, t.object_class, when,
                                      zone_id=zone_id)

    def _sweep_unchained(self) -> None:
        """Seal any event whose clip never finished.

        A camera that drops out mid-capture would otherwise leave its event
        permanently unsealed and therefore permanently unsynced - a silent hole
        in the record, which is the one failure this system must not have.
        """
        pending_ids = {c.event_id for c in self._pending_clips}
        for ev in self.db.list_events(limit=40, camera_id=self.camera.camera_id):
            if ev.ledger_index or ev.event_id in pending_ids:
                continue
            ev.detail["evidence_state"] = (
                "sealed without a clip: post-roll capture did not complete"
            )
            self.db.update_event(self.ledger.append(ev))
            log.warning("sealed %s without a clip (capture did not complete)",
                        ev.event_id)

    def _advance_pending_clips(self, latest: tuple[float, bytes] | None) -> None:
        if not latest or not self._pending_clips:
            return
        ts, jpeg = latest
        still_pending = []
        for clip in self._pending_clips:
            clip.push(ts, jpeg)
            if clip.complete:
                self._finalise_clip(clip)
            else:
                still_pending.append(clip)
        self._pending_clips = still_pending

    def _finalise_clip(self, clip: PendingClip) -> None:
        event = self.db.get_event(clip.event_id)
        if event is None:
            return
        when = event.timestamp
        path, digest, duration, size = self.evidence_store.write_clip(
            self.camera.camera_id, clip.event_id, when, clip.frames,
            self.source.nominal_fps,
        )
        if path is None:
            return
        ref = event.evidence or EvidenceRef()
        ref.clip_path = str(path)
        ref.clip_sha256 = digest
        ref.clip_seconds = duration
        ref.size_bytes = size
        event.evidence = ref
        event.detail.pop("evidence_state", None)
        # Evidence package complete: seal it into the chain. `append` is a no-op
        # if this event was already sealed by the sweeper.
        self.db.update_event(self.ledger.append(event))
        log.info("evidence clip written for %s (%.1fs, %d bytes)",
                 clip.event_id, duration, size)

    # -- rendering -------------------------------------------------------
    def _render(self, image: np.ndarray) -> None:
        canvas = image.copy()
        h, w = canvas.shape[:2]

        for zone in self.zones:
            if not zone.enabled or len(zone.points) < 2:
                continue
            pts = np.array([[p.x * w, p.y * h] for p in zone.points], dtype=np.int32)
            if zone.kind.value == "line":
                cv2.line(canvas, tuple(pts[0]), tuple(pts[-1]), (70, 120, 255), 2, cv2.LINE_AA)
            else:
                colour = (90, 200, 120) if zone.is_lawful_route else (70, 120, 255)
                cv2.polylines(canvas, [pts], True, colour, 1, cv2.LINE_AA)
            cv2.putText(canvas, zone.name, tuple(pts[0]), cv2.FONT_HERSHEY_SIMPLEX,
                        0.42, (200, 220, 240), 1, cv2.LINE_AA)

        for t in self._tracks:
            colour = BOX_COLOUR.get(t.object_class, DEFAULT_BOX_COLOUR)
            b = t.bbox
            cv2.rectangle(canvas, (int(b.x1), int(b.y1)), (int(b.x2), int(b.y2)),
                          colour, 2)
            label = f"{t.object_class.value} #{t.track_id}  {t.confidence:.0%}"
            cv2.putText(canvas, label, (int(b.x1), max(12, int(b.y1) - 6)),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.45, colour, 1, cv2.LINE_AA)
            if len(t.history) > 2:
                trail = np.array([[p.x, p.y] for p in t.history], dtype=np.int32)
                cv2.polylines(canvas, [trail], False, colour, 1, cv2.LINE_AA)

        banner = (f"{self.camera.camera_id}  |  {self.state.upper()}  |  "
                  f"{self.measured_fps:.1f} fps  |  {len(self._tracks)} tracked")
        cv2.rectangle(canvas, (0, 0), (w, 22), (18, 20, 24), -1)
        cv2.putText(canvas, banner, (8, 15), cv2.FONT_HERSHEY_SIMPLEX, 0.44,
                    (210, 220, 235), 1, cv2.LINE_AA)

        if self.state == "profiling":
            msg = (f"PROFILING - measuring optics and self-calibrating ground plane "
                   f"({len(self.profiler.acc.ground)} observations)")
            cv2.putText(canvas, msg, (8, h - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                        (120, 200, 255), 1, cv2.LINE_AA)

        ok, buf = cv2.imencode(".jpg", canvas, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
        if ok:
            self._latest_jpeg = buf.tobytes()

    # -- event emission --------------------------------------------------
    async def _emit_event(self, cand: EventCandidate) -> None:
        now = datetime.now(timezone.utc)
        track = cand.track

        # Scope the pattern-of-life lookup to the zone the event occurred in.
        # Judging a restricted-zone entry against camera-wide traffic compares it
        # against all the routine movement the camera sees elsewhere in frame,
        # and scores a genuine intrusion as "routine for this time" - which is
        # exactly backwards, since a restricted zone is restricted precisely
        # because nothing normally happens in it.
        verdict = self.normalcy.evaluate(
            self.camera.camera_id,
            track.object_class if track else ObjectClass.UNKNOWN,
            now,
            zone_id=cand.zone.zone_id if cand.zone else "",
        )
        fb_true, fb_false = self.db.feedback_for(
            self.camera.camera_id, cand.event_type.value)

        sctx = ScoringContext(
            is_night=bool(self.certificate and self.certificate.measurement.is_low_light),
            normalcy_ratio=verdict.ratio,
            normalcy_samples=verdict.samples,
            feedback_true=fb_true,
            feedback_false=fb_false,
            detection_confidence=track.confidence if track else 0.0,
            capability_supported=True,
            dwell_seconds=track.dwell_seconds if track else 0.0,
            speed_mps=track.speed_mps if track else None,
            group_size=cand.group_size,
            near_boundary=cand.near_boundary,
            extra=cand.extra_factors,
        )
        score, factors, priority = score_event(cand.event_type, track, sctx)

        event = Event(
            event_id="EVT-" + uuid.uuid4().hex[:12].upper(),
            camera_id=self.camera.camera_id,
            node_id=self.settings.node_id,
            event_type=cand.event_type,
            priority=priority,
            priority_score=score,
            priority_factors=factors,
            object_class=track.object_class if track else ObjectClass.UNKNOWN,
            track_id=track.track_id if track else None,
            confidence=track.confidence if track else 0.0,
            zone_id=cand.zone.zone_id if cand.zone else None,
            zone_name=cand.zone.name if cand.zone else None,
            direction_deg=track.direction_deg if track else None,
            speed_mps=track.speed_mps if track else None,
            timestamp=now,
            monotonic_ns=time.monotonic_ns(),
            summary=cand.summary,
            detail={**cand.detail,
                    "normalcy": verdict.detail,
                    "dedupe_key": cand.dedupe_key},
        )

        # Trigger frame now; the clip completes a few seconds later.
        clip_queued = False
        latest = self.buffer.latest()
        if latest:
            frame_path, thumb_path, digest = self.evidence_store.write_frame(
                self.camera.camera_id, event.event_id, now, latest[1])
            event.evidence = EvidenceRef(
                frame_path=str(frame_path), thumb_path=str(thumb_path),
                frame_sha256=digest,
            )
            if len(self._pending_clips) < MAX_PENDING_CLIPS:
                clip_queued = True
                self._pending_clips.append(PendingClip(
                    event_id=event.event_id,
                    pre_roll=self.buffer.snapshot(),
                    trigger_ts=latest[0],
                    after_seconds=self.settings.event_clip_after_seconds,
                    fps=self.source.nominal_fps,
                ))
            else:
                # Never let evidence capture consume unbounded memory. The event
                # and its trigger frame are kept; only the clip is skipped.
                event.detail["clip_skipped"] = (
                    f"more than {MAX_PENDING_CLIPS} clips were already being "
                    f"captured on this camera; trigger frame retained"
                )
                log.warning("clip backlog full on %s; skipping clip for %s",
                            self.camera.camera_id, event.event_id)

        # Recording and alerting are separate decisions. Everything below is
        # recorded; the governor decides only whether it interrupts anybody.
        if self.governor is not None:
            decision = self.governor.decide(event)
            event.alerted = decision.alerted
            event.alert_decision = decision.reason

        # An event is sealed into the hash chain once its evidence package is
        # complete, because the chain commits to the clip hash and the clip does
        # not exist yet. Until then the event is recorded, visible and alertable
        # - it is simply not yet shippable to the core, which cannot verify an
        # unsealed record. `_sweep_unchained` seals stragglers.
        if clip_queued:
            event.detail["evidence_state"] = "capturing post-roll; not yet sealed"
            self.db.insert_event(event)
        else:
            self.db.insert_event(self.ledger.append(event))

        log.info("[%s] %s %s (score %.2f)%s", self.camera.camera_id,
                 priority.value.upper(), cand.event_type.value, score,
                 "" if event.alerted else "  [recorded, not alerted]")

        await self.bus.publish(
            subj(self.settings.node_id, "events", self.camera.camera_id),
            {"event": event.model_dump(mode="json")},
        )

    async def _publish_live(self) -> None:
        await self.bus.publish(
            subj(self.settings.node_id, "tracks", self.camera.camera_id),
            {
                "camera_id": self.camera.camera_id,
                "state": self.state,
                "fps": round(self.measured_fps, 1),
                "health": self.camera.health.value,
                "tracks": [t.model_dump(mode="json") for t in self._tracks],
            },
        )

    # -- introspection ---------------------------------------------------
    @property
    def measured_fps(self) -> float:
        if not self._fps_window:
            return 0.0
        mean = sum(self._fps_window) / len(self._fps_window)
        return 1.0 / mean if mean > 1e-6 else 0.0

    @property
    def latest_jpeg(self) -> bytes | None:
        return self._latest_jpeg

    def status(self) -> dict[str, Any]:
        return {
            "camera_id": self.camera.camera_id,
            "name": self.camera.name,
            "state": self.state,
            "health": self.camera.health.value,
            "fps": round(self.measured_fps, 1),
            "nominal_fps": self.source.nominal_fps,
            "tracks": len(self._tracks),
            "detections": len(self._detections),
            "role": self.camera.role.value,
            "border_profile": self.camera.border_profile.value,
            "certificate_id": self.certificate.certificate_id if self.certificate else None,
            "granted": sorted(c.value for c in self.certificate.granted())
                       if self.certificate else [],
            "dori": self.certificate.overall_dori.value if self.certificate else None,
            "profiling_progress": (self.profiler.progress
                                   if self.state == "profiling" else None),
            "pending_clips": len(self._pending_clips),
            "zones": len(self.zones),
            "source": self.source.describe(),
        }
