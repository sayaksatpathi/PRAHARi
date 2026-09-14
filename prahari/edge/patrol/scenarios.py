"""The five patrol scenarios, as data.

Written once and consumed three times: by the unit tests, by
`scripts/verify_patrol.py`, and by the `/api/patrols/scenarios` endpoint the
dashboard renders. That matters more than it looks. A demo whose expected
outcomes live in the demo script can be made to say anything; here the expected
decision is declared beside the input, and every consumer runs the same real
`PatrolMatcher` over it. If the policy changes, these stop passing.

The reference time is fixed and timezone-naive on purpose, so a scenario means
the same thing on a machine in Kolkata and a CI runner in UTC. Patrol windows
are local-time by design (see `matcher._as_local`); pinning the scenarios to
naive local time takes the ambiguity out of the fixtures rather than out of the
feature.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from prahari.common.models import (
    ObjectClass,
    PatrolDecision,
    PatrolProfile,
    PatrolWindow,
)
from prahari.edge.patrol.matcher import Observation, PatrolMatcher
from prahari.edge.patrol.roster import PatrolRoster

# A night foot patrol: north fence, approach track, gate. 02:00-03:00, walking
# inbound from the fence, half an hour of grace.
SCENARIO_PATROL_ID = "SCEN-NIGHT-FOOT"
SCENARIO_DATE = datetime(2026, 3, 17)          # a Tuesday, fixed


def scenario_profile() -> PatrolProfile:
    return PatrolProfile(
        patrol_id=SCENARIO_PATROL_ID,
        name="Night Foot Patrol - North Fence",
        sector="BOP-EDGE-01",
        cameras=["CAM-014", "CAM-022", "CAM-011"],
        zones=["CAM-014-ZONE-1", "CAM-022-ROUTE-1", "CAM-011-ZONE-1"],
        windows=[PatrolWindow(start_minute=2 * 60, end_minute=3 * 60,
                              tolerance_minutes=30, label="night patrol")],
        expected_heading_deg=270.0,
        heading_tolerance_deg=70.0,
        object_classes=[ObjectClass.PERSON],
        max_group_size=4,
        min_speed_mps=0.4,
        max_speed_mps=2.2,
        notes="Reference profile for the five demonstration scenarios.",
        metadata={"scenario": True},
    )


def _at(hour: int, minute: int) -> datetime:
    return SCENARIO_DATE.replace(hour=hour, minute=minute)


@dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    narrative: str
    observation: Observation
    expected: PatrolDecision
    expected_effect: str


def scenarios() -> list[Scenario]:
    """The five cases, in the order they are specified and demonstrated."""
    return [
        Scenario(
            key="A",
            title="Normal patrol",
            narrative=(
                "The declared patrol walks its own route, in its own zone, "
                "inside its window, in the expected direction, at a walking "
                "pace."),
            observation=Observation(
                camera_id="CAM-014", when=_at(2, 30),
                object_class=ObjectClass.PERSON,
                zone_id="CAM-014-ZONE-1", zone_name="Restricted Zone",
                heading_deg=270.0, speed_mps=1.3, group_size=2),
            expected=PatrolDecision.SUPPRESSED,
            expected_effect=(
                "Recorded, scored, sealed and synchronised in full - and the "
                "operator is not interrupted."),
        ),
        Scenario(
            key="B",
            title="Patrol running late",
            narrative=(
                "Everything matches except the clock: the same movement, on "
                "the same route, twenty minutes after the window closed - "
                "inside the tolerance the profile allows for a patrol running "
                "behind."),
            observation=Observation(
                camera_id="CAM-014", when=_at(3, 20),
                object_class=ObjectClass.PERSON,
                zone_id="CAM-014-ZONE-1", zone_name="Restricted Zone",
                heading_deg=270.0, speed_mps=1.2, group_size=2),
            expected=PatrolDecision.DOWNGRADED,
            expected_effect=(
                "Kept as a lower-priority alert. Off schedule, a match can "
                "never be strong enough to suppress, however well everything "
                "else agrees."),
        ),
        Scenario(
            key="C",
            title="Patrol walking the wrong way",
            narrative=(
                "On the route, in the window, in the right zone - but heading "
                "back out along the fence instead of in. Either the patrol is "
                "lost or in trouble, or this is not the patrol."),
            observation=Observation(
                camera_id="CAM-014", when=_at(2, 30),
                object_class=ObjectClass.PERSON,
                zone_id="CAM-014-ZONE-1", zone_name="Restricted Zone",
                heading_deg=90.0, speed_mps=1.4, group_size=2),
            expected=PatrolDecision.DEVIATION,
            expected_effect=(
                "Escalated above an ordinary alert. This is the case a "
                "whitelist gets exactly backwards."),
        ),
        Scenario(
            key="D",
            title="Patrol off its route",
            narrative=(
                "In the window, on a patrol camera, moving the right way - but "
                "in a zone this patrol is not routed through."),
            observation=Observation(
                camera_id="CAM-014", when=_at(2, 40),
                object_class=ObjectClass.PERSON,
                zone_id="CAM-014-LINE-1", zone_name="Border Line",
                heading_deg=270.0, speed_mps=1.5, group_size=1),
            expected=PatrolDecision.DEVIATION,
            expected_effect=(
                "Escalated. The patrol layer knows where this patrol is "
                "entitled to be, and this is not it."),
        ),
        Scenario(
            key="E",
            title="Unknown person, no patrol due",
            narrative=(
                "The same camera, the same zone, the same direction, the same "
                "unhurried pace - at 21:00, when no patrol is out. Visually "
                "indistinguishable from scenario A."),
            observation=Observation(
                camera_id="CAM-014", when=_at(21, 0),
                object_class=ObjectClass.PERSON,
                zone_id="CAM-014-ZONE-1", zone_name="Restricted Zone",
                heading_deg=270.0, speed_mps=1.3, group_size=1),
            expected=PatrolDecision.NOT_MATCHED,
            expected_effect=(
                "Normal alert pipeline, untouched. Looking exactly like the "
                "patrol is not evidence of being the patrol when no patrol is "
                "due out - which is the whole reason the schedule is a gate "
                "rather than one cue among several."),
        ),
    ]


def run_scenarios() -> list[dict]:
    """Run all five through a real matcher and report what it actually decided.

    Uses a throwaway roster and matcher, so running this never touches the live
    node's suppression budgets.
    """
    roster = PatrolRoster()
    roster.upsert(scenario_profile())
    matcher = PatrolMatcher(roster)

    results = []
    for scen in scenarios():
        assessment = matcher.assess(scen.observation)
        results.append({
            "key": scen.key,
            "title": scen.title,
            "narrative": scen.narrative,
            "camera_id": scen.observation.camera_id,
            "zone": scen.observation.zone_name,
            "observed_time": scen.observation.when.strftime("%H:%M"),
            "observed_heading_deg": scen.observation.heading_deg,
            "expected_decision": scen.expected.value,
            "actual_decision": assessment.decision.value,
            "passed": assessment.decision is scen.expected,
            "match_score": assessment.match_score,
            "identity_confidence": assessment.identity_confidence,
            "conformance": assessment.conformance,
            "score_adjustment": assessment.score_adjustment,
            "reason": assessment.reason,
            "expected_effect": scen.expected_effect,
            "expected_window": assessment.expected_window,
            "expected_route": assessment.expected_route,
            "deviations": assessment.deviations,
        })
    return results
