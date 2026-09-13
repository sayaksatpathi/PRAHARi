"""Multi-object tracker, ByteTrack-style.

Tracking is what turns detections into events. Without it, "a person is in the
restricted zone" fires once per frame forever; with it, the same person is one
track with a direction, a dwell time and a trajectory, and therefore one event.

The association follows ByteTrack's central idea: run a second matching pass
against the *low-confidence* detections that a conventional tracker throws away.
At a border that matters more than it does on a benchmark, because the low-
confidence detections are exactly what a distant figure in poor light produces,
and dropping them is how a tracker loses the target it most needed to keep.

Implemented without scipy - greedy matching on the IoU matrix is within noise of
the Hungarian assignment at these object counts, and one fewer dependency on an
edge node is worth more than the difference.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from prahari.common.geometry import heading_deg
from prahari.common.models import BBox, Detection, ObjectClass, Point, Track


@dataclass
class _TrackState:
    track_id: int
    object_class: ObjectClass
    bbox: BBox
    confidence: float
    first_seen: datetime
    last_seen: datetime
    history: list[tuple[float, float]] = field(default_factory=list)
    timestamps: list[float] = field(default_factory=list)
    hits: int = 1
    lost_frames: int = 0
    confirmed: bool = False
    # Constant-velocity estimate of the foot point, in pixels per frame.
    vx: float = 0.0
    vy: float = 0.0
    # Per-class vote history: a track whose class flickers between person and
    # cattle should settle on the majority rather than whatever the last frame
    # happened to say.
    class_votes: dict[ObjectClass, int] = field(default_factory=dict)
    zones: set[str] = field(default_factory=set)
    zone_entry_times: dict[str, float] = field(default_factory=dict)
    # Rules that have already fired for this track, so one crossing is one event.
    fired: set[str] = field(default_factory=set)
    # Side of each tripwire on the previous update, for crossing detection.
    # Only updated when the track is clear of the line by a margin, so detector
    # jitter around the boundary cannot manufacture a crossing.
    line_sides: dict[str, int] = field(default_factory=dict)
    # Consecutive frames a track has appeared to be on the other side of a zone
    # boundary from its recorded membership. Membership only changes once this
    # is sustained.
    zone_streak: dict[str, int] = field(default_factory=dict)

    def predicted_bbox(self) -> BBox:
        return BBox(
            x1=self.bbox.x1 + self.vx, y1=self.bbox.y1 + self.vy,
            x2=self.bbox.x2 + self.vx, y2=self.bbox.y2 + self.vy,
        )

    def dominant_class(self) -> ObjectClass:
        if not self.class_votes:
            return self.object_class
        return max(self.class_votes.items(), key=lambda kv: kv[1])[0]


class ByteTracker:
    def __init__(
        self,
        camera_id: str,
        match_iou: float = 0.30,
        low_match_iou: float = 0.15,
        high_confidence: float = 0.55,
        max_lost_frames: int = 30,
        min_hits_to_confirm: int = 3,
        history_length: int = 90,
    ) -> None:
        self.camera_id = camera_id
        self.match_iou = match_iou
        self.low_match_iou = low_match_iou
        self.high_confidence = high_confidence
        self.max_lost_frames = max_lost_frames
        self.min_hits_to_confirm = min_hits_to_confirm
        self.history_length = history_length

        self._tracks: dict[int, _TrackState] = {}
        self._next_id = 1

    # -- matching --------------------------------------------------------
    @staticmethod
    def _greedy_match(
        track_ids: list[int], boxes: list[BBox],
        dets: list[Detection], threshold: float,
        predicted: dict[int, BBox],
    ) -> tuple[list[tuple[int, int]], set[int], set[int]]:
        """Greedy highest-IoU-first assignment."""
        pairs: list[tuple[float, int, int]] = []
        for ti, tid in enumerate(track_ids):
            pb = predicted[tid]
            for di, det in enumerate(dets):
                iou = pb.iou(det.bbox)
                if iou >= threshold:
                    pairs.append((iou, ti, di))
        pairs.sort(reverse=True)

        used_t: set[int] = set()
        used_d: set[int] = set()
        matches: list[tuple[int, int]] = []
        for _, ti, di in pairs:
            if ti in used_t or di in used_d:
                continue
            used_t.add(ti)
            used_d.add(di)
            matches.append((ti, di))

        unmatched_t = {i for i in range(len(track_ids)) if i not in used_t}
        unmatched_d = {i for i in range(len(dets)) if i not in used_d}
        return matches, unmatched_t, unmatched_d

    # -- update ----------------------------------------------------------
    def update(self, detections: list[Detection], now: datetime,
               timestamp_s: float) -> list[Track]:
        high = [d for d in detections if d.confidence >= self.high_confidence]
        low = [d for d in detections if d.confidence < self.high_confidence]

        active_ids = list(self._tracks.keys())
        predicted = {tid: self._tracks[tid].predicted_bbox() for tid in active_ids}
        boxes = [self._tracks[tid].bbox for tid in active_ids]

        # --- pass 1: confident detections against all tracks ---------------
        matches, unmatched_t, unmatched_d = self._greedy_match(
            active_ids, boxes, high, self.match_iou, predicted
        )
        for ti, di in matches:
            self._absorb(self._tracks[active_ids[ti]], high[di], now, timestamp_s)

        # --- pass 2: ByteTrack's recovery pass ------------------------------
        # Whatever is still unmatched gets a second chance against the weak
        # detections. This is what keeps a distant figure in poor light alive
        # through the frames where the detector is barely confident.
        remaining_ids = [active_ids[ti] for ti in sorted(unmatched_t)]
        if remaining_ids and low:
            rem_boxes = [self._tracks[tid].bbox for tid in remaining_ids]
            m2, unmatched_t2, _ = self._greedy_match(
                remaining_ids, rem_boxes, low, self.low_match_iou, predicted
            )
            for ti, di in m2:
                self._absorb(self._tracks[remaining_ids[ti]], low[di], now, timestamp_s)
            still_lost = [remaining_ids[ti] for ti in sorted(unmatched_t2)]
        else:
            still_lost = remaining_ids

        for tid in still_lost:
            t = self._tracks[tid]
            t.lost_frames += 1
            # Coast the prediction forward so a re-acquisition after a short
            # occlusion still matches.
            t.bbox = t.predicted_bbox()

        # --- new tracks -----------------------------------------------------
        for di in sorted(unmatched_d):
            self._spawn(high[di], now, timestamp_s)

        # --- retire ---------------------------------------------------------
        for tid in [t for t, s in self._tracks.items() if s.lost_frames > self.max_lost_frames]:
            del self._tracks[tid]

        return self.tracks()

    def _spawn(self, det: Detection, now: datetime, timestamp_s: float) -> None:
        tid = self._next_id
        self._next_id += 1
        st = _TrackState(
            track_id=tid,
            object_class=det.object_class,
            bbox=det.bbox,
            confidence=det.confidence,
            first_seen=now,
            last_seen=now,
        )
        st.history.append(det.bbox.foot)
        st.timestamps.append(timestamp_s)
        st.class_votes[det.object_class] = 1
        self._tracks[tid] = st

    def _absorb(self, st: _TrackState, det: Detection, now: datetime,
                timestamp_s: float) -> None:
        prev_foot = st.bbox.foot
        st.bbox = det.bbox
        st.confidence = det.confidence
        st.last_seen = now
        st.hits += 1
        st.lost_frames = 0
        st.class_votes[det.object_class] = st.class_votes.get(det.object_class, 0) + 1
        st.object_class = st.dominant_class()
        if st.hits >= self.min_hits_to_confirm:
            st.confirmed = True

        foot = det.bbox.foot
        # Exponential smoothing on velocity: raw frame-to-frame deltas are too
        # noisy to drive a direction rule, and an unsmoothed heading makes a
        # wrong-direction rule fire on jitter alone.
        st.vx = 0.7 * st.vx + 0.3 * (foot[0] - prev_foot[0])
        st.vy = 0.7 * st.vy + 0.3 * (foot[1] - prev_foot[1])

        st.history.append(foot)
        st.timestamps.append(timestamp_s)
        if len(st.history) > self.history_length:
            st.history = st.history[-self.history_length:]
            st.timestamps = st.timestamps[-self.history_length:]

    # -- export ----------------------------------------------------------
    def _heading(self, st: _TrackState, lookback: int = 8) -> float | None:
        if len(st.history) < 2:
            return None
        a = st.history[max(0, len(st.history) - lookback - 1)]
        b = st.history[-1]
        if math.hypot(b[0] - a[0], b[1] - a[1]) < 1.5:
            return None          # below the noise floor; call it stationary
        return heading_deg(a, b)

    def tracks(self, confirmed_only: bool = True) -> list[Track]:
        out: list[Track] = []
        for st in self._tracks.values():
            if confirmed_only and not st.confirmed:
                continue
            dwell = 0.0
            if len(st.timestamps) >= 2:
                dwell = st.timestamps[-1] - st.timestamps[0]
            out.append(Track(
                track_id=st.track_id,
                camera_id=self.camera_id,
                object_class=st.object_class,
                first_seen=st.first_seen,
                last_seen=st.last_seen,
                bbox=st.bbox,
                confidence=st.confidence,
                history=[Point(x=p[0], y=p[1]) for p in st.history[-40:]],
                direction_deg=self._heading(st),
                dwell_seconds=round(dwell, 2),
                zones=sorted(st.zones),
                hits=st.hits,
                lost_frames=st.lost_frames,
            ))
        return out

    def state(self, track_id: int) -> _TrackState | None:
        return self._tracks.get(track_id)

    def states(self, confirmed_only: bool = True) -> list[_TrackState]:
        return [s for s in self._tracks.values() if s.confirmed or not confirmed_only]

    def __len__(self) -> int:
        return len(self._tracks)
