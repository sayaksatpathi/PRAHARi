"""Border event rule engine.

Two doctrines ship, selected per camera, because India's borders are not one
problem:

  FENCED  - a managed, fenced border. Crossing the line is itself the event, so
            tripwires, restricted zones and direction rules carry the weight.

  OPEN    - an open border, as the Indo-Nepal and Indo-Bhutan frontiers are by
            treaty. Lawful crossing is constant and heavy. A tripwire here fires
            thousands of times a day and teaches the operator to ignore it, so
            what matters instead is movement *away* from lawful routes, at hours
            the camera does not normally see traffic, by entities that keep
            coming back.

Both packs feed the same scorer and the same evidence path. A camera can be
switched between them without touching anything downstream.

The engine's other job is restraint. A detection is not an event, and an event
is not an alert. One person crossing one line produces exactly one event, no
matter how many frames they are visible for - enforced through per-track fired
markers, because the fastest way to lose an operator is to send the same alert
forty times.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from prahari.common.geometry import (
    angular_difference,
    compass_label,
    distance_to_polyline,
    point_in_polygon,
    side_of_line,
)
from prahari.common.models import (
    BorderProfile,
    Camera,
    Capability,
    CapabilityCertificate,
    EventType,
    Track,
    Zone,
    ZoneKind,
)

log = logging.getLogger("prahari.rules")


@dataclass
class RuleContext:
    camera: Camera
    zones: list[Zone]
    certificate: CapabilityCertificate | None
    now: datetime
    timestamp_s: float
    frame_width: int
    frame_height: int
    is_night: bool = False
    loitering_threshold_s: float = 30.0
    group_size_threshold: int = 3
    group_window_s: float = 20.0
    cooldown_s: float = 30.0
    # Hysteresis. A detector's box jitters by several pixels frame to frame, and
    # a track standing near a boundary will appear to cross it repeatedly. Both
    # of these exist to stop that jitter being reported as movement.
    zone_confirm_frames: int = 3
    line_margin: float = 0.012        # normalised distance from the tripwire

    def normalised(self, x: float, y: float) -> tuple[float, float]:
        """Image pixels -> 0..1, the space zones are stored in."""
        return (x / max(1, self.frame_width), y / max(1, self.frame_height))

    def capability(self, cap: Capability) -> bool:
        return self.certificate.is_granted(cap) if self.certificate else True


@dataclass
class EventCandidate:
    event_type: EventType
    track: Track | None
    zone: Zone | None
    summary: str
    dedupe_key: str
    extra_factors: dict[str, float] = field(default_factory=dict)
    detail: dict[str, Any] = field(default_factory=dict)
    near_boundary: bool = False
    group_size: int = 1


class RuleEngine:
    """Evaluates one camera's tracks against its zones, once per processed frame."""

    def __init__(self, camera_id: str) -> None:
        self.camera_id = camera_id
        # Recent zone entries, for the group-movement window.
        self._zone_entries: dict[str, list[tuple[int, float]]] = {}
        # When each dedupe key last produced an event.
        self._last_fired: dict[str, float] = {}

    def _within_cooldown(self, key: str, now_s: float, cooldown_s: float) -> bool:
        last = self._last_fired.get(key)
        return last is not None and (now_s - last) < cooldown_s

    def _prune_cooldowns(self, now_s: float, cooldown_s: float) -> None:
        horizon = now_s - cooldown_s * 4
        if len(self._last_fired) > 512:
            self._last_fired = {k: v for k, v in self._last_fired.items() if v >= horizon}

    # -- entry point -----------------------------------------------------
    def evaluate(self, tracker, tracks: list[Track], ctx: RuleContext) -> list[EventCandidate]:
        return self._suppress_duplicates(self._raw_evaluate(tracker, tracks, ctx), ctx)

    def _suppress_duplicates(self, candidates: list[EventCandidate],
                             ctx: RuleContext) -> list[EventCandidate]:
        """Enforce the dedupe key.

        Track-scoped rules mark themselves fired on the track, but rules with no
        single owning track - group movement above all - have no such anchor, and
        without this they re-fire on every processed frame for as long as the
        condition holds. In testing that turned one group of four people walking
        into a zone into several hundred identical events, which is precisely the
        alert flood this system claims to prevent.
        """
        kept: list[EventCandidate] = []
        for cand in candidates:
            if self._within_cooldown(cand.dedupe_key, ctx.timestamp_s, ctx.cooldown_s):
                continue
            self._last_fired[cand.dedupe_key] = ctx.timestamp_s
            kept.append(cand)
        self._prune_cooldowns(ctx.timestamp_s, ctx.cooldown_s)
        return kept

    def _raw_evaluate(self, tracker, tracks: list[Track],
                      ctx: RuleContext) -> list[EventCandidate]:
        out: list[EventCandidate] = []
        active = [z for z in ctx.zones if z.enabled]

        for track in tracks:
            state = tracker.state(track.track_id)
            if state is None:
                continue
            self._update_zone_membership(state, track, active, ctx)

            if ctx.camera.border_profile is BorderProfile.FENCED:
                out.extend(self._fenced_rules(state, track, active, ctx))
            else:
                out.extend(self._open_border_rules(state, track, active, ctx))

            # Applies under both doctrines.
            out.extend(self._loitering(state, track, active, ctx))

        out.extend(self._group_movement(tracks, active, ctx))
        return out

    # -- shared bookkeeping ----------------------------------------------
    def _update_zone_membership(self, state, track: Track, zones: list[Zone],
                                ctx: RuleContext) -> None:
        """Track which polygons the object is standing in.

        Membership is tested on the *foot point*, not the box centre. A person
        halfway up a frame has a box centre well above the ground they are
        actually standing on, and testing the centre puts them in the wrong zone
        by several metres at range.
        """
        fx, fy = ctx.normalised(*track.bbox.foot)
        for zone in zones:
            if zone.kind is not ZoneKind.POLYGON and not zone.is_lawful_route:
                continue
            if len(zone.points) < 3:
                continue
            inside = point_in_polygon(fx, fy, zone.points)
            was_inside = zone.zone_id in state.zones

            if inside == was_inside:
                state.zone_streak[zone.zone_id] = 0
                continue

            # The apparent state differs from the recorded one. Require it to
            # persist before acting: without this, a person standing on the edge
            # of a zone re-enters it every few frames, and three such people
            # manufacture a "group movement" event out of nothing.
            streak = state.zone_streak.get(zone.zone_id, 0) + 1
            state.zone_streak[zone.zone_id] = streak
            if streak < ctx.zone_confirm_frames:
                continue
            state.zone_streak[zone.zone_id] = 0

            if inside:
                state.zones.add(zone.zone_id)
                state.zone_entry_times[zone.zone_id] = ctx.timestamp_s
                self._zone_entries.setdefault(zone.zone_id, []).append(
                    (track.track_id, ctx.timestamp_s)
                )
            else:
                state.zones.discard(zone.zone_id)
                state.zone_entry_times.pop(zone.zone_id, None)

    @staticmethod
    def _fire_once(state, key: str) -> bool:
        """True the first time a given rule fires for a given track."""
        if key in state.fired:
            return False
        state.fired.add(key)
        return True

    # -- fenced-border doctrine ------------------------------------------
    def _fenced_rules(self, state, track: Track, zones: list[Zone],
                      ctx: RuleContext) -> list[EventCandidate]:
        out: list[EventCandidate] = []
        fx, fy = ctx.normalised(*track.bbox.foot)
        cls_name = track.object_class.value.title()

        for zone in zones:
            # --- virtual tripwire ---------------------------------------
            if zone.kind is ZoneKind.LINE and len(zone.points) >= 2:
                # Only take a reading when the track is clear of the line by a
                # margin. A track sitting on the boundary flickers between sides
                # on detector noise alone, and recording those flickers turned
                # ambient traffic that never approached the fence into a steady
                # stream of crossing events.
                if distance_to_polyline((fx, fy), zone.points) < ctx.line_margin:
                    continue
                side = side_of_line((fx, fy), zone.points[0], zone.points[1])
                prev = state.line_sides.get(zone.zone_id)
                state.line_sides[zone.zone_id] = side
                if prev is not None and side != 0 and prev != 0 and side != prev:
                    key = "cross:" + zone.zone_id
                    if self._fire_once(state, key):
                        inbound = side < 0
                        bearing = compass_label(track.direction_deg)
                        out.append(EventCandidate(
                            event_type=EventType.LINE_CROSSING,
                            track=track, zone=zone,
                            summary=(
                                cls_name + " crossed " + zone.name + " "
                                + ("inbound" if inbound else "outbound")
                                + ", heading " + bearing
                            ),
                            dedupe_key=f"{self.camera_id}:{track.track_id}:{key}",
                            extra_factors={"inbound crossing": 0.10} if inbound else {},
                            detail={"direction": bearing, "inbound": inbound},
                            near_boundary=True,
                        ))

            # --- restricted area ----------------------------------------
            elif zone.kind is ZoneKind.POLYGON and not zone.is_lawful_route:
                if zone.zone_id not in state.zones:
                    continue

                key = "intrude:" + zone.zone_id
                if self._fire_once(state, key):
                    is_vehicle = track.object_class.is_vehicle
                    out.append(EventCandidate(
                        event_type=(EventType.VEHICLE_MOVEMENT if is_vehicle
                                    else EventType.ZONE_INTRUSION),
                        track=track, zone=zone,
                        summary=cls_name + " entered " + zone.name,
                        dedupe_key=f"{self.camera_id}:{track.track_id}:{key}",
                        detail={"zone": zone.name},
                    ))

                # --- wrong-direction, only inside the zone that defines it
                if (zone.direction_deg is not None
                        and track.direction_deg is not None
                        and ctx.capability(Capability.DIRECTION_ANALYSIS)):
                    deviation = angular_difference(
                        track.direction_deg, zone.direction_deg)
                    if deviation > zone.direction_tolerance_deg:
                        key = "wrongdir:" + zone.zone_id
                        if self._fire_once(state, key):
                            out.append(EventCandidate(
                                event_type=EventType.WRONG_DIRECTION,
                                track=track, zone=zone,
                                summary=(
                                    cls_name + " moving "
                                    + compass_label(track.direction_deg)
                                    + " against the "
                                    + compass_label(zone.direction_deg)
                                    + " flow expected in " + zone.name
                                ),
                                dedupe_key=f"{self.camera_id}:{track.track_id}:{key}",
                                detail={"deviation_deg": round(deviation, 1)},
                            ))
        return out

    # -- open-border doctrine --------------------------------------------
    def _open_border_rules(self, state, track: Track, zones: list[Zone],
                           ctx: RuleContext) -> list[EventCandidate]:
        """On an open border the lawful route is the baseline, not the trigger.

        Everyone crossing at the designated point is going about their business.
        What is worth an operator's attention is someone who is deliberately not
        using it.
        """
        routes = [z for z in zones if z.kind is ZoneKind.ROUTE or z.is_lawful_route]
        if not routes:
            return []

        fx, fy = ctx.normalised(*track.bbox.foot)

        def off_route_distance(z: Zone) -> float:
            if len(z.points) >= 3 and point_in_polygon(fx, fy, z.points):
                return 0.0
            return distance_to_polyline((fx, fy), z.points)

        offset = min(off_route_distance(z) for z in routes)
        if offset < 0.10:
            return []          # on or skirting the route; not notable

        # A track has to be established before this fires: a single noisy
        # detection in scrub is not a person avoiding a checkpost.
        if track.hits < 8:
            return []

        if not self._fire_once(state, "offroute"):
            return []

        nearest = min(routes, key=off_route_distance)
        return [EventCandidate(
            event_type=EventType.OFF_ROUTE_MOVEMENT,
            track=track, zone=nearest,
            summary=(
                track.object_class.value.title()
                + " moving off the lawful route (" + nearest.name
                + "), heading " + compass_label(track.direction_deg)
            ),
            dedupe_key=f"{self.camera_id}:{track.track_id}:offroute",
            extra_factors={"distance off route": round(min(0.15, offset * 0.4), 3)},
            detail={"offset_normalised": round(offset, 3), "route": nearest.name},
        )]

    # -- applies under both doctrines ------------------------------------
    def _loitering(self, state, track: Track, zones: list[Zone],
                   ctx: RuleContext) -> list[EventCandidate]:
        out: list[EventCandidate] = []
        for zone_id, entered_at in list(state.zone_entry_times.items()):
            dwell = ctx.timestamp_s - entered_at
            if dwell < ctx.loitering_threshold_s:
                continue
            key = "loiter:" + zone_id
            if not self._fire_once(state, key):
                continue
            zone = next((z for z in zones if z.zone_id == zone_id), None)
            where = zone.name if zone else "the monitored area"
            out.append(EventCandidate(
                event_type=EventType.LOITERING,
                track=track, zone=zone,
                summary=(
                    track.object_class.value.title() + " has remained in "
                    + where + f" for {dwell:.0f} seconds"
                ),
                dedupe_key=f"{self.camera_id}:{track.track_id}:{key}",
                detail={"dwell_seconds": round(dwell, 1)},
            ))
        return out

    def _group_movement(self, tracks: list[Track], zones: list[Zone],
                        ctx: RuleContext) -> list[EventCandidate]:
        """Several people entering the same zone inside one time window.

        Scored separately from individual intrusion because group movement at an
        unusual hour is a materially different signal from one person, and
        reporting four separate intrusions loses exactly the pattern that
        mattered.
        """
        out: list[EventCandidate] = []
        for zone in zones:
            # A designated crossing route exists precisely so that groups of
            # people walk through it. Flagging that is the open-border mistake
            # this whole doctrine split was created to avoid.
            if zone.is_lawful_route or zone.kind is ZoneKind.ROUTE:
                continue
            entries = self._zone_entries.get(zone.zone_id, [])
            # Forget entries older than the window, so the list cannot grow
            # without bound on a busy camera.
            entries = [(tid, ts) for tid, ts in entries
                       if ctx.timestamp_s - ts <= ctx.group_window_s]
            self._zone_entries[zone.zone_id] = entries
            if len(entries) < ctx.group_size_threshold:
                continue

            member_ids = sorted({tid for tid, _ in entries})
            # Key on the zone alone. Keying on the member set looked more precise
            # but re-fired every time the composition shifted by one track - and
            # track membership churns constantly as people are lost and
            # re-acquired, so one group milling around a zone produced a steady
            # stream of events. Operationally there is one situation here ("a
            # group is in this zone") and it deserves one alert per cooldown.
            key = "group:" + zone.zone_id
            lead = next((t for t in tracks if t.track_id == member_ids[0]), None)
            out.append(EventCandidate(
                event_type=EventType.GROUP_MOVEMENT,
                track=lead, zone=zone,
                summary=(
                    f"{len(member_ids)} subjects entered {zone.name} within "
                    f"{ctx.group_window_s:.0f} seconds"
                ),
                dedupe_key=f"{self.camera_id}:{key}",
                detail={"track_ids": member_ids},
                group_size=len(member_ids),
            ))
        return out
