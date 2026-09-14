"""Patrol matching and the suppression decision.

The problem this solves is the one that actually decommissions border video
analytics. A BOP runs its own patrols along the fence, on a schedule, through
the same zones the rules watch - so the system's most reliable, most repeatable
alert is its own side walking past. On a fenced sector that is routinely the
single largest category of false alarm, and it arrives every night at the same
hour, which is exactly the pattern that teaches an operator to stop looking.

The tempting fix is a whitelist: mark the patrol camera, mute it between 02:00
and 03:00. That is also how you build a hole in your own perimeter, because
anybody who learns the schedule inherits the exemption. So this module does not
implement a whitelist. It implements a *conformance test*, and the difference is
the whole design:

*   Suppression requires agreement on every cue at once - the right camera, the
    right zone, the right window, the right direction, plausible movement. Any
    one of them failing is enough to withhold suppression.

*   Matching the patrol's identity while breaking its expectations is its own
    outcome, and it raises the priority. A patrol walking its route backwards,
    or off it, or at a sprint, is either lost, in trouble, or not the patrol.
    All three are worth more attention than an ordinary alert, not less.

*   Suppression is budgeted per window. A patrol is a handful of transits, not
    an open licence. Once a profile has spent its budget the remaining
    look-alikes go through the normal pipeline, so walking in behind the patrol
    buys an intruder one downgrade, not an evening of immunity.

*   Nothing is deleted, hidden or unrecorded. A suppressed event is written,
    scored, sealed into the hash chain with its full assessment attached, and
    synchronised. It simply did not ring a bell. The assessment says which
    profile matched, on which cues, and what the expected values were, so the
    decision is reviewable after the fact - which is the minimum bar for a
    system that is allowed to decide not to tell a human something.

None of the thresholds here are calibrated against real border footage. They are
policy settings with visible arithmetic, not learned parameters, and they are
presented as such.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from prahari.common.geometry import angular_difference
from prahari.common.models import (
    ObjectClass,
    PatrolAssessment,
    PatrolCue,
    PatrolDecision,
    PatrolProfile,
    PatrolWindow,
)

log = logging.getLogger("prahari.patrol")

_ONE_DAY = timedelta(days=1)

# --- decision thresholds ---------------------------------------------------
# A strong match must clear a deliberately high bar, because the cost of a
# wrong suppression is an unreported crossing and the cost of a wrong
# non-suppression is one more alert in a system that already rations them.
STRONG_MATCH = 0.72
UNCERTAIN_MATCH = 0.45

# Off schedule, a match can never be strong, whatever the other cues say. This
# is the rule that keeps "it looked exactly like the patrol" from being enough
# on its own at an hour when no patrol was due out.
OFF_SCHEDULE_CAP = 0.65

# To call something a *deviation* rather than an unknown, we must first be
# confident it is the patrol at all. Below this, a violated expectation just
# means it probably was not the patrol, and the event takes the normal path.
DEVIATION_IDENTITY_MIN = 0.70

# Score adjustments contributed to the Event Priority Score. They annotate; the
# rest of the scorer is untouched.
SUPPRESS_ADJUSTMENT = -0.35
DOWNGRADE_ADJUSTMENT = -0.16
DEVIATION_ADJUSTMENT = +0.16

# Cue weights. Time carries the most because a schedule is the one thing about a
# patrol that is declared in advance rather than inferred from imagery.
W_TIME = 0.34
W_ZONE = 0.22
W_DIRECTION = 0.22
W_ROUTE = 0.12
W_BEHAVIOUR = 0.10


@dataclass
class Observation:
    """What the pipeline knows about one event, in patrol-matching terms.

    A plain structure rather than the Event itself, so the matcher can be tested
    and reasoned about without a database, a camera or a frame.
    """
    camera_id: str
    when: datetime
    object_class: ObjectClass = ObjectClass.UNKNOWN
    zone_id: str | None = None
    zone_name: str | None = None
    heading_deg: float | None = None
    speed_mps: float | None = None
    group_size: int = 1
    dwell_seconds: float = 0.0
    plate: str | None = None
    # Recent camera sequence for this entity, oldest first, from the
    # cross-camera coordinator when it has one. Absent is normal, not an error.
    route_trail: list[str] = field(default_factory=list)


@dataclass
class PatrolMetrics:
    """Counters for what the patrol layer actually did.

    Every number here is a count of events this node processed in this run.
    None of it is a benchmark, and `alert_reduction_percent` is defined
    narrowly: the share of assessed events that the patrol layer removed from
    the alert path. It is not a false-alarm-rate improvement, because that would
    require ground truth this node does not have.
    """
    raw_events: int = 0
    matched: int = 0
    suppressed: int = 0
    downgraded: int = 0
    deviations: int = 0
    unmatched: int = 0
    budget_exhausted: int = 0

    def record(self, decision: PatrolDecision) -> None:
        self.raw_events += 1
        if decision is PatrolDecision.SUPPRESSED:
            self.matched += 1
            self.suppressed += 1
        elif decision is PatrolDecision.DOWNGRADED:
            self.matched += 1
            self.downgraded += 1
        elif decision is PatrolDecision.DEVIATION:
            self.matched += 1
            self.deviations += 1
        else:
            self.unmatched += 1

    def as_dict(self) -> dict:
        raw = self.raw_events
        return {
            "raw_events": raw,
            "patrol_matched_events": self.matched,
            "suppressed_events": self.suppressed,
            "downgraded_events": self.downgraded,
            "abnormal_patrol_events": self.deviations,
            "unmatched_events": self.unmatched,
            "suppression_budget_exhausted": self.budget_exhausted,
            "alert_reduction_percent": (
                round(100.0 * self.suppressed / raw, 1) if raw else 0.0),
            "definition": (
                "alert_reduction_percent is the share of assessed events this "
                "node removed from the alert path because a declared patrol "
                "accounted for them. It is a count of this run, not a "
                "benchmark, and not a false-alarm-rate measurement - that "
                "would need ground truth the node does not have."),
        }


def _window_key(patrol_id: str, when: datetime, window: PatrolWindow) -> str:
    """Identify one occurrence of a window, for budgeting.

    A window that wraps midnight belongs to the day it started on, so a patrol
    that goes out at 23:40 and comes back at 00:20 spends one budget, not two.
    """
    day = when.date()
    if window.wraps_midnight and (when.hour * 60 + when.minute) <= window.end_minute:
        day = (when - _ONE_DAY).date()
    return f"{patrol_id}:{day.isoformat()}:{window.start_minute}"


class PatrolMatcher:
    """Assesses events against the declared patrol roster.

    Thread-safe: camera pipelines run in their own executor threads and all
    assess against the same matcher, which carries mutable budget state.
    """

    def __init__(self, roster, metrics: PatrolMetrics | None = None) -> None:
        self.roster = roster
        self.metrics = metrics or PatrolMetrics()
        self._lock = threading.RLock()
        self._spent: dict[str, int] = {}

    # -- public ----------------------------------------------------------
    def assess(self, obs: Observation) -> PatrolAssessment:
        """Evaluate one observation against every active patrol profile.

        Returns the best-matching assessment, or an unmatched one. Always
        returns something: "no patrol accounts for this" is a finding, and the
        operator UI shows it as such rather than as silence.
        """
        with self._lock:
            candidates = [p for p in self.roster.active_for(obs.camera_id)
                          if self._class_allowed(p, obs)]
            best: PatrolAssessment | None = None
            for profile in candidates:
                assessment = self._assess_one(profile, obs)
                if assessment is None:
                    continue
                if best is None or assessment.match_score > best.match_score:
                    best = assessment

            if best is None:
                best = PatrolAssessment(
                    decision=PatrolDecision.NOT_MATCHED,
                    candidates_considered=len(candidates),
                    observed_camera=obs.camera_id,
                    observed_zone=obs.zone_id,
                    observed_time=obs.when,
                    observed_heading_deg=obs.heading_deg,
                    reason=("no active patrol profile covers this camera, object "
                            "class and time; the event takes the normal alert path")
                    if not candidates else
                    (f"{len(candidates)} patrol profile(s) cover this camera and "
                     f"object class, but none was on duty within its tolerance of "
                     f"this time, or one was ruled out by an identifier that did "
                     f"not match; the event takes the normal alert path"),
                )
            else:
                best.candidates_considered = len(candidates)

            self.metrics.record(best.decision)
            return best

    def spend_budget(self, assessment: PatrolAssessment, obs: Observation) -> None:
        """Charge one suppression against the matching profile's window budget.

        Called by the caller *after* it has acted on a SUPPRESSED decision, so
        an assessment that is only inspected - a dry run, a test, the scenario
        harness - does not consume a real patrol's allowance.
        """
        if assessment.decision is not PatrolDecision.SUPPRESSED:
            return
        profile = self.roster.get(assessment.patrol_id or "")
        if profile is None:
            return
        local = _as_local(obs.when)
        window, gap = profile.window_for(local)
        if window is None or gap > 0:
            return
        with self._lock:
            key = _window_key(profile.patrol_id, local, window)
            self._spent[key] = self._spent.get(key, 0) + 1
            if len(self._spent) > 512:
                self._prune(local)

    def budget_state(self) -> dict[str, int]:
        with self._lock:
            return dict(self._spent)

    def reset_budgets(self) -> None:
        with self._lock:
            self._spent.clear()

    # -- internals -------------------------------------------------------
    def _prune(self, now: datetime) -> None:
        keep = {now.date().isoformat(), (now - _ONE_DAY).date().isoformat()}
        self._spent = {k: v for k, v in self._spent.items()
                       if k.split(":")[1] in keep}

    @staticmethod
    def _class_allowed(profile: PatrolProfile, obs: Observation) -> bool:
        if not profile.object_classes:
            return True
        return obs.object_class in profile.object_classes

    def _assess_one(self, profile: PatrolProfile,
                    obs: Observation) -> PatrolAssessment | None:
        local = _as_local(obs.when)

        # --- identity disqualifier -------------------------------------
        # Only consulted when the deployment explicitly enabled identity for
        # this profile. A plate that was read and is not one of the patrol's is
        # positive evidence that this is not the patrol, so the profile stops
        # being a candidate rather than merely scoring lower.
        if (profile.identity_matching_enabled and profile.vehicle_plates
                and obs.plate):
            if _normalise_plate(obs.plate) not in {
                    _normalise_plate(p) for p in profile.vehicle_plates}:
                return None

        cues: list[PatrolCue] = []

        # --- time ------------------------------------------------------
        window, gap = profile.window_for(local)
        if window is None:
            time_score = 0.0
            cues.append(PatrolCue(
                name="schedule", score=0.0, weight=W_TIME, evaluable=False,
                detail="this patrol has no scheduled window configured"))
            on_schedule = False
        else:
            tolerance = max(1, window.tolerance_minutes)
            if gap > tolerance:
                # Schedule is a hard gate, not a weak cue. Past the tolerance
                # band there is no longer any reason to believe this is the
                # patrol, and every other cue agreeing is not evidence that it
                # is - a stranger walking the patrol route in the patrol's
                # direction looks identical to the patrol, which is exactly the
                # observation this layer must not be allowed to act on. The
                # profile stops being a candidate and the event takes the
                # normal path.
                return None
            if gap == 0.0:
                time_score, on_schedule = 1.0, True
                detail = f"inside the scheduled window {window.describe()}"
            else:
                time_score, on_schedule = max(0.0, 1.0 - gap / tolerance), False
                detail = (f"{gap:.0f} min outside {window.describe()}, within the "
                          f"{tolerance} min tolerance - late, not scheduled")
            cues.append(PatrolCue(name="schedule", score=round(time_score, 3),
                                  weight=W_TIME, detail=detail))

        # --- zone / route entitlement ----------------------------------
        camera_zones = [z for z in profile.zones if z.startswith(obs.camera_id)]
        off_route = False
        if not profile.zones:
            cues.append(PatrolCue(
                name="route zone", score=0.0, weight=W_ZONE, evaluable=False,
                detail="this patrol declares no zones, so entitlement cannot be judged"))
            zone_score = 0.0
        elif obs.zone_id is None:
            cues.append(PatrolCue(
                name="route zone", score=0.0, weight=W_ZONE, evaluable=False,
                detail="the event is not tied to a zone"))
            zone_score = 0.0
        elif obs.zone_id in profile.zones:
            zone_score = 1.0
            cues.append(PatrolCue(
                name="route zone", score=1.0, weight=W_ZONE,
                detail=f"{obs.zone_name or obs.zone_id} is on the patrol's declared route"))
        else:
            zone_score = 0.0
            # Only an actual departure from the route if the patrol declares
            # zones on *this* camera. Otherwise we simply have nothing to say.
            off_route = bool(camera_zones)
            cues.append(PatrolCue(
                name="route zone", score=0.0, weight=W_ZONE,
                evaluable=bool(camera_zones),
                detail=(f"{obs.zone_name or obs.zone_id} is not on the patrol's route "
                        f"for this camera (expected {', '.join(camera_zones)})"
                        if camera_zones else
                        "the patrol declares no zones on this camera")))

        # --- direction --------------------------------------------------
        direction_opposed = False
        if profile.expected_heading_deg is None or obs.heading_deg is None:
            cues.append(PatrolCue(
                name="direction", score=0.0, weight=W_DIRECTION, evaluable=False,
                detail=("no expected heading is configured for this patrol"
                        if profile.expected_heading_deg is None else
                        "the track has no usable heading yet")))
            direction_score = 0.0
        else:
            diff = angular_difference(obs.heading_deg, profile.expected_heading_deg)
            tol = max(1.0, profile.heading_tolerance_deg)
            direction_score = max(0.0, 1.0 - diff / tol)
            # "Clearly the other way", not merely "outside tolerance". A track
            # whose heading is noisy near the edge of tolerance is a weak match;
            # one heading back down its own route is a different claim entirely.
            direction_opposed = diff >= 90.0 and diff > tol
            cues.append(PatrolCue(
                name="direction", score=round(direction_score, 3), weight=W_DIRECTION,
                detail=(f"heading {obs.heading_deg:.0f} deg against an expected "
                        f"{profile.expected_heading_deg:.0f} deg "
                        f"(+/-{tol:.0f}); {diff:.0f} deg apart")))

        # --- route consistency ------------------------------------------
        route_score, route_evaluable, route_detail = _route_consistency(profile, obs)
        cues.append(PatrolCue(name="route consistency", score=round(route_score, 3),
                              weight=W_ROUTE, evaluable=route_evaluable,
                              detail=route_detail))

        # --- movement behaviour ------------------------------------------
        behaviour_score, violations, behaviour_detail = _behaviour(profile, obs)
        cues.append(PatrolCue(name="movement", score=round(behaviour_score, 3),
                              weight=W_BEHAVIOUR, detail=behaviour_detail))

        # --- combine -----------------------------------------------------
        # Two separate questions, kept separate on purpose: *is this the
        # patrol*, and *is it behaving like the patrol*. Collapsing them into
        # one number is what makes a system unable to tell a routine patrol from
        # a patrol in trouble.
        identity = _weighted(
            [(time_score, 0.60, window is not None),
             (route_score, 0.40, route_evaluable)])
        conformance = _weighted(
            [(direction_score, 0.55, profile.expected_heading_deg is not None
              and obs.heading_deg is not None),
             (zone_score, 0.30, bool(profile.zones) and obs.zone_id is not None),
             (behaviour_score, 0.15, True)])

        match = 0.5 * identity + 0.5 * conformance
        if not on_schedule:
            match = min(match, OFF_SCHEDULE_CAP)
        match = max(0.0, min(1.0, match))

        deviations: list[str] = []
        if direction_opposed:
            deviations.append("moving against the patrol's expected direction")
        if off_route:
            deviations.append("outside the zones this patrol is routed through")
        deviations.extend(violations)

        assessment = PatrolAssessment(
            patrol_id=profile.patrol_id,
            patrol_name=profile.name,
            match_score=round(match, 3),
            identity_confidence=round(identity, 3),
            conformance=round(conformance, 3),
            cues=cues,
            deviations=deviations,
            expected_route=list(profile.cameras),
            expected_window=window.describe() if window else None,
            expected_heading_deg=profile.expected_heading_deg,
            observed_time=obs.when,
            observed_heading_deg=obs.heading_deg,
            observed_camera=obs.camera_id,
            observed_zone=obs.zone_name or obs.zone_id,
        )
        self._decide(assessment, profile, obs, local, window, gap,
                     identity, deviations)
        return assessment

    def _decide(self, a: PatrolAssessment, profile: PatrolProfile,
                obs: Observation, local: datetime,
                window: PatrolWindow | None, gap: float,
                identity: float, deviations: list[str]) -> None:
        """Apply the decision policy to a scored assessment, in order."""

        # 1. Deviation first. Being confidently the patrol and visibly not
        #    behaving like it outranks every other outcome, including a high
        #    overall score - which is precisely the case a whitelist gets wrong,
        #    because to a whitelist a patrol walking backwards is still the
        #    patrol.
        if identity >= DEVIATION_IDENTITY_MIN and deviations:
            a.decision = PatrolDecision.DEVIATION
            a.score_adjustment = DEVIATION_ADJUSTMENT
            a.reason = (
                f"this matches {profile.name} on schedule and route, but "
                f"{_join(deviations)}. A patrol that is not behaving like the "
                f"patrol is escalated, not suppressed.")
            return

        # 2. Strong, on-schedule, conforming match: do not interrupt anyone.
        if a.match_score >= STRONG_MATCH:
            key = (_window_key(profile.patrol_id, local, window)
                   if window is not None and gap == 0 else None)
            spent = self._spent.get(key, 0) if key else 0
            if key is not None and spent >= profile.max_suppressions_per_window:
                a.decision = PatrolDecision.DOWNGRADED
                a.score_adjustment = DOWNGRADE_ADJUSTMENT
                a.reason = (
                    f"consistent with {profile.name}, but that patrol has already "
                    f"accounted for {spent} events in this window and its limit is "
                    f"{profile.max_suppressions_per_window}. Further look-alikes are "
                    f"retained at reduced priority rather than suppressed, so a "
                    f"patrol route cannot be used as cover.")
                self.metrics.budget_exhausted += 1
                return
            a.decision = PatrolDecision.SUPPRESSED
            a.score_adjustment = SUPPRESS_ADJUSTMENT
            a.reason = (
                f"conforms to {profile.name} on every cue that could be checked "
                f"(match {a.match_score:.2f}): right camera, right zone, inside "
                f"the scheduled window, expected direction. Recorded and sealed "
                f"in full; the operator was not interrupted.")
            return

        # 3. Plausible but not certain: keep it, lower it, say why.
        if a.match_score >= UNCERTAIN_MATCH:
            a.decision = PatrolDecision.DOWNGRADED
            a.score_adjustment = DOWNGRADE_ADJUSTMENT
            weak = [c.name for c in a.cues if c.evaluable and c.score < 0.6]
            a.reason = (
                f"probably {profile.name} (match {a.match_score:.2f}), but not "
                f"certainly: {_join(weak) if weak else 'the evidence is thin'}. "
                f"Retained at reduced priority rather than suppressed.")
            return

        # 4. Not enough to act on. Normal pipeline, unchanged.
        a.decision = PatrolDecision.NOT_MATCHED
        a.score_adjustment = 0.0
        a.reason = (
            f"{profile.name} was considered and did not account for this "
            f"(match {a.match_score:.2f}, below {UNCERTAIN_MATCH:.2f}); the event "
            f"takes the normal alert path.")


# =====================================================================
# Cue helpers
# =====================================================================

def _weighted(parts: Iterable[tuple[float, float, bool]]) -> float:
    """Weighted mean over the cues that were actually evaluable.

    Dividing by the total possible weight instead would penalise a camera for
    lacking a capability it was never certified for - a thermal unit with no
    direction analysis would score every patrol as a poor match purely because
    one cue could not be computed.
    """
    earned = possible = 0.0
    for score, weight, evaluable in parts:
        if not evaluable:
            continue
        earned += score * weight
        possible += weight
    return earned / possible if possible > 0 else 0.0


def _route_consistency(profile: PatrolProfile,
                       obs: Observation) -> tuple[float, bool, str]:
    """Did this entity arrive here the way the patrol route would bring it?

    Uses the cross-camera trail when one exists. Absent, the cue is simply not
    evaluable - most events have no trail, and treating that as disagreement
    would make single-camera events permanently unmatchable.
    """
    trail = [c for c in obs.route_trail if c]
    if len(trail) < 2 or len(profile.cameras) < 2:
        return 0.0, False, "no cross-camera trail for this entity"

    order = {cam: i for i, cam in enumerate(profile.cameras)}
    known = [order[c] for c in trail if c in order]
    if len(known) < 2:
        return 0.0, True, (
            f"this entity came via {' -> '.join(trail[-3:])}, which is not on the "
            f"patrol route {' -> '.join(profile.cameras)}")

    forward = sum(1 for a, b in zip(known, known[1:]) if b > a)
    steps = len(known) - 1
    score = forward / steps if steps else 0.0
    off_route_hops = len(trail) - len(known)
    detail = (f"walked {' -> '.join(trail[-4:])}; {forward} of {steps} hops follow "
              f"the patrol route order")
    if off_route_hops:
        score *= max(0.0, 1.0 - off_route_hops / len(trail))
        detail += f", {off_route_hops} hop(s) off the route entirely"
    return score, True, detail


def _behaviour(profile: PatrolProfile,
               obs: Observation) -> tuple[float, list[str], str]:
    """Movement plausibility, and any violation severe enough to be a deviation."""
    score = 1.0
    violations: list[str] = []
    notes: list[str] = []

    if obs.group_size > profile.max_group_size:
        score -= 0.5
        violations.append(
            f"{obs.group_size} subjects moving together, where this patrol is at "
            f"most {profile.max_group_size}")
        notes.append(f"group of {obs.group_size}")
    elif obs.group_size > 1:
        notes.append(f"group of {obs.group_size}, within the patrol's strength")

    if obs.speed_mps is not None:
        if obs.speed_mps > profile.max_speed_mps * 2.0:
            score -= 0.5
            violations.append(
                f"moving at {obs.speed_mps:.1f} m/s, far above this patrol's "
                f"{profile.max_speed_mps:.1f} m/s")
            notes.append(f"{obs.speed_mps:.1f} m/s")
        elif not (profile.min_speed_mps <= obs.speed_mps <= profile.max_speed_mps):
            score -= 0.25
            notes.append(
                f"{obs.speed_mps:.1f} m/s, outside the expected "
                f"{profile.min_speed_mps:.1f}-{profile.max_speed_mps:.1f} m/s")
        else:
            notes.append(f"{obs.speed_mps:.1f} m/s, patrol pace")
    else:
        notes.append("no speed estimate (camera has no ground plane)")

    return (max(0.0, score), violations,
            "; ".join(notes) if notes else "nothing notable about the movement")


def _as_local(when: datetime) -> datetime:
    """Patrol schedules are written in local time; events are stamped in UTC.

    A duty roster saying 02:00 means 02:00 at the post. Converting here, once,
    keeps every window comparison in the same frame.
    """
    if when.tzinfo is None:
        return when
    return when.astimezone()


def _normalise_plate(plate: str) -> str:
    return "".join(ch for ch in plate.upper() if ch.isalnum())


def _join(items: list[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]
