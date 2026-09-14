"""Deterministic evaluation harness.

Drives the real pipeline components - profiling, detection, tracking, rules,
scoring - synchronously against a source, and scores the result. Synchronous and
single-threaded on purpose: an evaluation must be reproducible, and the async,
bus-driven production pipeline is not the place to measure from. The components
being exercised are exactly the production ones; only the plumbing around them is
the harness's own.

Two modes, both honest about what they measure:

  simulated source  - the simulator emits ground truth, so detection recall,
                      event correctness and detection latency are all scorable,
                      and camera height is known so profiling accuracy is too.
                      Detection numbers here characterise the *synthetic
                      detector's modelled behaviour* (its miss rate at range, its
                      noise-driven false positives), not a trained network.

  real source + real model - the same harness, the same metrics, now measuring a
                      real detector on real imagery. This is where the numbers
                      become claims about accuracy. It needs footage the harness
                      does not ship; see scripts/fetch_datasets.py.

The one metric that is meaningful in *both* modes, and the one that matters most
for this system, is the event-level score: did the right alarm fire, in time,
without a flood of false ones. That depends on the whole pipeline, not on the
detector alone, and the simulator's ambient traffic and degradation are real.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from prahari.common.models import (
    BBox, Camera, CapabilityCertificate, Event, EventType, ObjectClass,
    Priority, Track,
)
from prahari.edge.alerting import AlertGovernor
from prahari.edge.detect.base import Detector
from prahari.edge.priority import ScoringContext, score_event
from prahari.edge.profiling.certificate import issue_certificate
from prahari.edge.profiling.measure import CameraProfiler
from prahari.edge.rules.engine import RuleContext, RuleEngine
from prahari.edge.sources.base import VideoSource
from prahari.edge.track.bytetrack import ByteTracker
from prahari.eval.tracking import (
    TrackingScorer, ground_truth_as_pairs, tracks_as_pairs,
)
from prahari.eval.metrics import DetectionCounts, EventScoring, match_detections


@dataclass
class ScenarioResult:
    camera_id: str
    detector_name: str
    detector_simulated: bool
    source_simulated: bool
    frames: int
    run_seconds: float
    wall_seconds: float

    detection: DetectionCounts
    events: EventScoring

    profiling_true_height_m: float | None
    profiling_recovered_height_m: float | None
    ground_plane_recovered: bool

    mean_ms_per_frame: float
    throughput_fps: float
    granted_capabilities: list[str]

    # Present only where the ground truth carries identities, i.e. real MOT
    # sequences. None means "not scored", never "scored as zero".
    tracking: dict[str, Any] | None = None
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        height_err = None
        if (self.profiling_true_height_m and self.profiling_recovered_height_m):
            height_err = round(
                abs(self.profiling_recovered_height_m - self.profiling_true_height_m)
                / self.profiling_true_height_m * 100, 1)
        return {
            "camera_id": self.camera_id,
            "detector": {"name": self.detector_name,
                         "simulated": self.detector_simulated},
            "source_simulated": self.source_simulated,
            "frames": self.frames,
            "run_seconds": round(self.run_seconds, 1),
            "wall_seconds": round(self.wall_seconds, 1),
            "detection": self.detection.as_dict(),
            "tracking": self.tracking,
            "events": self.events.as_dict(self.run_seconds),
            "profiling": {
                "ground_plane_recovered": self.ground_plane_recovered,
                "true_height_m": self.profiling_true_height_m,
                "recovered_height_m": (round(self.profiling_recovered_height_m, 2)
                                       if self.profiling_recovered_height_m else None),
                "height_error_percent": height_err,
            },
            "performance": {
                "mean_ms_per_frame": round(self.mean_ms_per_frame, 1),
                "throughput_fps": round(self.throughput_fps, 1),
            },
            "granted_capabilities": self.granted_capabilities,
            "notes": self.notes,
        }


class EvaluationHarness:
    """Runs one camera through one scenario and scores it."""

    def __init__(
        self,
        camera: Camera,
        source: VideoSource,
        detector: Detector,
        zones,
        *,
        settings,
        true_camera_height_m: float | None = None,
        intruder_actor_label: str = "intruder",
    ) -> None:
        self.camera = camera
        self.source = source
        self.detector = detector
        self.zones = zones
        self.settings = settings
        self.true_camera_height_m = true_camera_height_m
        self.intruder_label = intruder_actor_label

        self.tracker = ByteTracker(
            camera.camera_id,
            match_iou=settings.track_match_iou,
            max_lost_frames=int(settings.track_timeout_seconds * source.nominal_fps),
        )
        self.rules = RuleEngine(camera.camera_id)
        self.profiler = CameraProfiler(
            camera.camera_id, min_frames=40, min_ground_samples=28,
            max_frames=int(source.nominal_fps * 300),
        )
        self.certificate: CapabilityCertificate | None = None
        self.governor = AlertGovernor(settings.alert_budget_per_hour)

    # -- running ---------------------------------------------------------
    def run(
        self,
        profile_frames: int,
        eval_frames: int,
        inject_intruder_at_frame: int | None = None,
        inject_fn=None,
    ) -> ScenarioResult:
        if not self.source.open():
            raise RuntimeError(f"could not open source for {self.camera.camera_id}")

        detection = DetectionCounts()
        events = EventScoring()
        frame_times: list[float] = []

        recovered_height = None
        wall_start = time.perf_counter()
        frame_index = 0
        sim_start_ts: float | None = None
        intruder_crossed_at: float | None = None
        first_true_event_at: float | None = None

        total = profile_frames + eval_frames
        while frame_index < total:
            frame = self.source.read()
            if frame is None:
                continue
            ts = frame.timestamp.timestamp()
            if sim_start_ts is None:
                sim_start_ts = ts

            if (inject_intruder_at_frame is not None
                    and frame_index == inject_intruder_at_frame
                    and inject_fn is not None):
                inject_fn(self.source)

            t0 = time.perf_counter()

            # --- profiling phase ---
            profiling = self.certificate is None
            allowed = None if not profiling else None  # detector-side gate not needed here

            detections = self.detector.infer(
                frame.image, allowed=None, frame_index=frame_index,
                context={"ground_truth": frame.ground_truth,
                         **self._detector_context()},
            )
            tracks = self.tracker.update(detections, frame.timestamp, ts)
            frame_times.append((time.perf_counter() - t0) * 1000.0)

            # --- detection scoring, once past the profiling warm-up ---
            if not profiling and frame.ground_truth:
                self._score_detections(detections, frame.ground_truth, detection)

            # --- feed the profiler; issue a certificate when ready ---
            if profiling:
                self.profiler.observe_frame(frame.image, ts)
                h, w = frame.image.shape[:2]
                for t in tracks:
                    if t.object_class is not ObjectClass.PERSON:
                        continue
                    if t.lost_frames > 0 or t.hits < 5:
                        continue
                    b = t.bbox
                    if b.y2 >= h - 2 or b.y1 <= 1 or b.x1 <= 1 or b.x2 >= w - 2:
                        continue
                    self.profiler.observe_person(foot_v=b.y2, px_height=b.height,
                                                 px_width=b.width)
                if self.profiler.ready:
                    self._issue_certificate(recovered_out=lambda v: None)
                    m = self.certificate.measurement
                    if m.ground_plane_estimated and m.px_per_metre_near > 0:
                        recovered_height = (m.height - 1 - m.horizon_y) / m.px_per_metre_near
                frame_index += 1
                continue

            # --- event evaluation phase ---
            intruder_box = self._intruder_box(frame.ground_truth)
            if intruder_box is not None and intruder_crossed_at is None:
                # Record when the intruder first appears; latency is measured
                # from here. (A tighter definition would use the line-crossing
                # instant; appearance is the conservative choice and never
                # understates latency.)
                intruder_crossed_at = ts

            candidates = self._evaluate_rules(tracks, frame)
            for cand in candidates:
                events.total_events += 1
                is_true = self._event_is_true(cand, tracks, intruder_box)

                # Real governor decision, not an approximation: the alerted
                # numbers are what an operator would actually have been
                # interrupted by. Normalcy is not applied here, so these are an
                # UPPER BOUND - the live node's learnt pattern of life damps the
                # rate further.
                score, _factors, priority = score_event(
                    cand.event_type, cand.track,
                    ScoringContext(
                        detection_confidence=cand.track.confidence if cand.track else 0.0,
                        near_boundary=cand.near_boundary,
                        group_size=cand.group_size,
                        extra=cand.extra_factors,
                    ),
                )
                decision = self.governor.decide(Event(
                    event_id=f"eval-{frame_index}-{events.total_events}",
                    camera_id=self.camera.camera_id, node_id="eval",
                    event_type=cand.event_type, priority=priority,
                    priority_score=score,
                ))
                if decision.alerted:
                    events.alerted_events += 1

                if is_true:
                    events.true_events += 1
                    events.intruder_detected = True
                    if decision.alerted:
                        events.alerted_true_events += 1
                    if first_true_event_at is None:
                        first_true_event_at = ts
                else:
                    events.false_alarm_events += 1
                    if decision.alerted:
                        events.alerted_false_alarms += 1

            frame_index += 1

        self.source.close()
        wall_seconds = time.perf_counter() - wall_start
        run_seconds = (frame_index / self.source.nominal_fps)

        if (intruder_crossed_at is not None
                and first_true_event_at is not None):
            events.detection_latency_s = first_true_event_at - intruder_crossed_at

        mean_ms = sum(frame_times) / len(frame_times) if frame_times else 0.0
        throughput = 1000.0 / mean_ms if mean_ms > 0 else 0.0

        granted = sorted(c.value for c in self.certificate.granted()) \
            if self.certificate else []

        notes = []
        if self.detector.describe().get("simulated"):
            notes.append(
                "Detector is the SYNTHETIC detector: detection precision/recall "
                "characterise its modelled miss rate and noise, not a trained "
                "network. Event-level and profiling metrics remain meaningful.")
        if not self.certificate:
            notes.append("No capability certificate issued during the run: too "
                         "few pedestrian observations to fit the ground plane.")

        return ScenarioResult(
            camera_id=self.camera.camera_id,
            detector_name=self.detector.describe().get("name", "unknown"),
            detector_simulated=bool(self.detector.describe().get("simulated")),
            source_simulated=bool(getattr(self.source, "is_simulated", False)),
            frames=frame_index,
            run_seconds=run_seconds,
            wall_seconds=wall_seconds,
            detection=detection,
            events=events,
            profiling_true_height_m=self.true_camera_height_m,
            profiling_recovered_height_m=recovered_height,
            ground_plane_recovered=bool(
                self.certificate
                and self.certificate.measurement.ground_plane_estimated),
            mean_ms_per_frame=mean_ms,
            throughput_fps=throughput,
            granted_capabilities=granted,
            notes=notes,
        )

    # -- detection-only mode (real footage without a border scenario) ----
    def run_detection_only(
        self,
        profile_frames: int,
        eval_frames: int,
    ) -> ScenarioResult:
        """Score detection, tracking, profiling and throughput on real footage.

        Used for datasets like MOT that are pedestrian scenes with per-frame
        ground truth but no border geometry - there is no fence to cross, so the
        event-level scenario metrics do not apply and are left empty. What this
        does measure, and what synthetic footage could not, is a real detector's
        precision and recall against real annotations, plus whether the ground
        plane self-calibrates on real people rather than simulated ones.
        """
        if not self.source.open():
            raise RuntimeError(f"could not open source for {self.camera.camera_id}")

        detection = DetectionCounts()
        # Identity-aware tracking metrics, scored on the same single pass.
        tracking = TrackingScorer()
        frame_times: list[float] = []
        recovered_height = None
        track_ids_seen: set[int] = set()
        max_concurrent = 0
        frame_index = 0

        wall_start = time.perf_counter()
        total = profile_frames + eval_frames
        while frame_index < total:
            frame = self.source.read()
            if frame is None:
                break                       # a finite sequence has ended
            ts = frame.timestamp.timestamp()

            t0 = time.perf_counter()
            detections = self.detector.infer(
                frame.image, allowed=None, frame_index=frame_index,
                context={"ground_truth": frame.ground_truth,
                         **self._detector_context()},
            )
            tracks = self.tracker.update(detections, frame.timestamp, ts)
            frame_times.append((time.perf_counter() - t0) * 1000.0)

            # Profiling runs during the warm-up window only, gated by frame index
            # rather than by "has a certificate yet". Real footage may never fit a
            # ground plane (no clean pedestrians, odd geometry), and gating on the
            # certificate then left the run stuck in profiling, scoring nothing.
            # Detection scoring must still happen once the window is past.
            profiling = frame_index < profile_frames
            if profiling:
                self.profiler.observe_frame(frame.image, ts)
                h, w = frame.image.shape[:2]
                for t in tracks:
                    if t.object_class is not ObjectClass.PERSON:
                        continue
                    if t.lost_frames > 0 or t.hits < 5:
                        continue
                    b = t.bbox
                    if b.y2 >= h - 2 or b.y1 <= 1 or b.x1 <= 1 or b.x2 >= w - 2:
                        continue
                    self.profiler.observe_person(foot_v=b.y2, px_height=b.height,
                                                 px_width=b.width)
                if self.certificate is None and self.profiler.ready:
                    self._issue_certificate(recovered_out=lambda v: None)
                    m = self.certificate.measurement
                    if m.ground_plane_estimated and m.px_per_metre_near > 0:
                        recovered_height = (m.height - 1 - m.horizon_y) / m.px_per_metre_near
            else:
                # Score detections against this frame's real ground truth.
                if frame.ground_truth:
                    self._score_detections(detections, frame.ground_truth, detection)
                    gt_pairs = ground_truth_as_pairs(frame.ground_truth)
                    if gt_pairs:
                        tracking.update(gt_pairs, tracks_as_pairs(
                            [t for t in tracks
                             if t.object_class is ObjectClass.PERSON]))
                track_ids_seen.update(t.track_id for t in tracks)
                max_concurrent = max(max_concurrent, len(tracks))

            frame_index += 1

        self.source.close()
        wall_seconds = time.perf_counter() - wall_start
        run_seconds = frame_index / max(1.0, self.source.nominal_fps)
        mean_ms = sum(frame_times) / len(frame_times) if frame_times else 0.0

        notes = [
            "Detection-only evaluation on real footage with real ground truth. "
            "Event-level scenario metrics do not apply (no border geometry) and "
            "are omitted.",
            f"{len(track_ids_seen)} distinct tracks over the run, up to "
            f"{max_concurrent} concurrent.",
        ]
        tracking_result = tracking.as_dict() if tracking.frames else None
        if tracking_result:
            notes.append(
                f"Tracking scored on {tracking_result['frames_scored']} frames "
                f"with identity ground truth: MOTA {tracking_result['mota']:.3f}, "
                f"IDF1 {tracking_result['idf1']:.3f}, "
                f"{tracking_result['id_switches']} ID switches over "
                f"{tracking_result['gt_tracks']} ground-truth tracks.")
        if self.detector.describe().get("simulated"):
            notes.append(
                "WARNING: the SYNTHETIC detector is loaded, which sees nothing in "
                "real footage. Install a real ONNX model (models/README.md) - "
                "these numbers are meaningless otherwise.")

        return ScenarioResult(
            camera_id=self.camera.camera_id,
            detector_name=self.detector.describe().get("name", "unknown"),
            detector_simulated=bool(self.detector.describe().get("simulated")),
            source_simulated=False,
            frames=frame_index,
            run_seconds=run_seconds,
            wall_seconds=wall_seconds,
            detection=detection,
            tracking=tracking_result,
            events=EventScoring(),
            profiling_true_height_m=None,
            profiling_recovered_height_m=recovered_height,
            ground_plane_recovered=bool(
                self.certificate
                and self.certificate.measurement.ground_plane_estimated),
            mean_ms_per_frame=mean_ms,
            throughput_fps=1000.0 / mean_ms if mean_ms > 0 else 0.0,
            granted_capabilities=sorted(c.value for c in self.certificate.granted())
            if self.certificate else [],
            notes=notes,
        )

    # -- helpers ---------------------------------------------------------
    def _detector_context(self) -> dict[str, Any]:
        if not self.certificate:
            return {}
        m = self.certificate.measurement
        return {
            "effective_resolution_factor": m.effective_resolution_factor,
            "noise_sigma": m.noise_sigma,
            "compression_artifact_score": m.compression_artifact_score,
            "is_low_light": m.is_low_light,
        }

    def _issue_certificate(self, recovered_out) -> None:
        measurement = self.profiler.build(claimed_fps=self.camera.claimed_fps)
        self.certificate = issue_certificate(self.camera, measurement, version=1)

    def _score_detections(self, detections, ground_truth, counts) -> None:
        preds = [(d.object_class, d.bbox) for d in detections]
        gts = []
        for gt in ground_truth:
            try:
                cls = ObjectClass(gt["object_class"])
            except ValueError:
                cls = ObjectClass.UNKNOWN
            x1, y1, x2, y2 = gt["bbox"]
            gts.append((cls, BBox(x1=x1, y1=y1, x2=x2, y2=y2)))
        match_detections(preds, gts, iou_threshold=0.4, counts=counts)

    def _intruder_box(self, ground_truth) -> BBox | None:
        for gt in ground_truth:
            if gt.get("label") == self.intruder_label:
                x1, y1, x2, y2 = gt["bbox"]
                return BBox(x1=x1, y1=y1, x2=x2, y2=y2)
        return None

    def _event_is_true(self, cand, tracks, intruder_box) -> bool:
        """An event is attributable to the incident if the track that raised it
        overlaps the injected intruder's ground-truth box this frame."""
        if intruder_box is None or cand.track is None:
            return False
        return cand.track.bbox.iou(intruder_box) >= 0.3

    def _evaluate_rules(self, tracks, frame):
        ctx = RuleContext(
            camera=self.camera,
            zones=self.zones,
            certificate=self.certificate,
            now=frame.timestamp,
            timestamp_s=frame.timestamp.timestamp(),
            frame_width=frame.image.shape[1],
            frame_height=frame.image.shape[0],
            is_night=bool(self.certificate
                          and self.certificate.measurement.is_low_light),
            loitering_threshold_s=self.settings.loitering_threshold_seconds,
            group_size_threshold=self.settings.group_size_threshold,
            group_window_s=self.settings.group_window_seconds,
            cooldown_s=self.settings.event_cooldown_seconds,
        )
        return self.rules.evaluate(self.tracker, tracks, ctx)
