"""Friendly-force suppression: policy, safeguards and the five scenarios.

The tests that matter most here are not the ones proving suppression works.
They are the ones proving it *refuses* to work - off schedule, off route,
against the expected direction, past the window budget, on a mismatched
identifier. A patrol layer that only ever suppresses is a whitelist with extra
steps, and the refusals are what separate the two.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from prahari.common.db import Database
from prahari.common.models import (
    Event,
    EventType,
    ObjectClass,
    PatrolDecision,
    PatrolProfile,
    PatrolWindow,
    Priority,
)
from prahari.edge.evidence import compute_entry_hash
from prahari.edge.patrol.matcher import (
    DEVIATION_ADJUSTMENT,
    Observation,
    PatrolMatcher,
    PatrolMetrics,
    STRONG_MATCH,
    SUPPRESS_ADJUSTMENT,
)
from prahari.edge.patrol.roster import PatrolRoster, demo_patrols, seed_demo_patrols
from prahari.edge.patrol.scenarios import (
    run_scenarios,
    scenario_profile,
    scenarios,
)


# =====================================================================
# Fixtures
# =====================================================================

@pytest.fixture()
def roster() -> PatrolRoster:
    r = PatrolRoster()
    r.upsert(scenario_profile())
    return r


@pytest.fixture()
def matcher(roster: PatrolRoster) -> PatrolMatcher:
    return PatrolMatcher(roster)


def obs(**kw) -> Observation:
    """A scenario-A observation, with overrides."""
    base = dict(
        camera_id="CAM-014",
        when=datetime(2026, 3, 17, 2, 30),
        object_class=ObjectClass.PERSON,
        zone_id="CAM-014-ZONE-1",
        zone_name="Restricted Zone",
        heading_deg=270.0,
        speed_mps=1.3,
        group_size=2,
    )
    base.update(kw)
    return Observation(**base)


# =====================================================================
# The five specified scenarios
# =====================================================================

@pytest.mark.parametrize("scenario", scenarios(), ids=lambda s: f"{s.key}-{s.key}")
def test_scenario_decides_as_specified(scenario):
    """Each reference scenario reaches the decision it is specified to reach."""
    r = PatrolRoster()
    r.upsert(scenario_profile())
    assessment = PatrolMatcher(r).assess(scenario.observation)
    assert assessment.decision is scenario.expected, (
        f"scenario {scenario.key} ({scenario.title}): expected "
        f"{scenario.expected.value}, got {assessment.decision.value} "
        f"- {assessment.reason}")


def test_scenario_harness_reports_all_five_passing():
    results = run_scenarios()
    assert len(results) == 5
    assert all(r["passed"] for r in results), [
        (r["key"], r["expected_decision"], r["actual_decision"]) for r in results]
    # Every decision must carry a reason an operator could act on.
    assert all(len(r["reason"]) > 40 for r in results)


def test_scenarios_span_every_decision_state():
    """The scenario set exercises all four outcomes, not three plus a repeat."""
    decisions = {r["actual_decision"] for r in run_scenarios()}
    assert decisions == {"suppressed", "downgraded", "deviation", "not_matched"}


# =====================================================================
# Suppression, and the score/alert effects
# =====================================================================

def test_conforming_patrol_is_suppressed_with_a_negative_adjustment(matcher):
    a = matcher.assess(obs())
    assert a.decision is PatrolDecision.SUPPRESSED
    assert a.match_score >= STRONG_MATCH
    assert a.score_adjustment == SUPPRESS_ADJUSTMENT
    assert a.patrol_id == scenario_profile().patrol_id


def test_assessment_records_expected_against_observed(matcher):
    """The operator must be able to review what was expected, not just the verdict."""
    a = matcher.assess(obs())
    assert a.expected_route == ["CAM-014", "CAM-022", "CAM-011"]
    assert a.expected_window and "02:00-03:00" in a.expected_window
    assert a.expected_heading_deg == 270.0
    assert a.observed_heading_deg == 270.0
    assert a.observed_camera == "CAM-014"
    assert a.observed_zone == "Restricted Zone"
    assert a.observed_time is not None
    # Every cue is reported, including the ones that could not be evaluated.
    names = {c.name for c in a.cues}
    assert {"schedule", "route zone", "direction", "movement"} <= names


# =====================================================================
# The refusals - the part that makes this not a whitelist
# =====================================================================

def test_off_schedule_can_never_be_suppressed(matcher):
    """Late but inside tolerance: still the patrol, still not suppressed."""
    a = matcher.assess(obs(when=datetime(2026, 3, 17, 3, 20)))
    assert a.decision is PatrolDecision.DOWNGRADED
    assert a.score_adjustment < 0


def test_identical_movement_outside_the_window_gets_the_normal_pipeline(matcher):
    """The scenario-E property, stated directly.

    Same camera, same zone, same heading, same pace as a suppressed patrol -
    but at an hour no patrol is due out. Nothing about it may be softened.
    """
    conforming = matcher.assess(obs())
    stranger = matcher.assess(obs(when=datetime(2026, 3, 17, 21, 0)))
    assert conforming.decision is PatrolDecision.SUPPRESSED
    assert stranger.decision is PatrolDecision.NOT_MATCHED
    assert stranger.score_adjustment == 0.0
    assert stranger.patrol_id is None


def test_wrong_direction_escalates_rather_than_suppresses(matcher):
    a = matcher.assess(obs(heading_deg=90.0))
    assert a.decision is PatrolDecision.DEVIATION
    assert a.score_adjustment == DEVIATION_ADJUSTMENT
    assert any("direction" in d for d in a.deviations)
    # It is still confidently the patrol - that is precisely why it escalates.
    assert a.identity_confidence >= 0.7


def test_off_route_zone_escalates(matcher):
    a = matcher.assess(obs(zone_id="CAM-014-LINE-1", zone_name="Border Line"))
    assert a.decision is PatrolDecision.DEVIATION
    assert any("zones" in d for d in a.deviations)


def test_camera_not_on_any_route_is_never_matched(matcher):
    a = matcher.assess(obs(camera_id="CAM-031"))
    assert a.decision is PatrolDecision.NOT_MATCHED
    assert a.candidates_considered == 0


def test_wrong_object_class_is_never_matched(matcher):
    """A foot patrol profile does not account for a truck."""
    a = matcher.assess(obs(object_class=ObjectClass.TRUCK))
    assert a.decision is PatrolDecision.NOT_MATCHED
    assert a.candidates_considered == 0


def test_inactive_profile_stops_matching_immediately(roster, matcher):
    assert matcher.assess(obs()).decision is PatrolDecision.SUPPRESSED
    roster.set_active(scenario_profile().patrol_id, False)
    assert matcher.assess(obs()).decision is PatrolDecision.NOT_MATCHED


def test_oversized_group_escalates(matcher):
    """Ten people on the patrol route at patrol time is not the two-man patrol."""
    a = matcher.assess(obs(group_size=10))
    assert a.decision is PatrolDecision.DEVIATION
    assert any("subjects moving together" in d for d in a.deviations)


def test_sprinting_on_the_patrol_route_escalates(matcher):
    a = matcher.assess(obs(speed_mps=9.0))
    assert a.decision is PatrolDecision.DEVIATION


def test_wrong_day_is_not_matched():
    """A Tuesday-only patrol says nothing about a Saturday."""
    profile = scenario_profile().model_copy(update={
        "windows": [PatrolWindow(start_minute=120, end_minute=180,
                                 tolerance_minutes=30, days=[1])]})
    r = PatrolRoster()
    r.upsert(profile)
    m = PatrolMatcher(r)
    assert m.assess(obs()).decision is PatrolDecision.SUPPRESSED          # Tuesday
    saturday = m.assess(obs(when=datetime(2026, 3, 21, 2, 30)))
    assert saturday.decision is PatrolDecision.NOT_MATCHED


# =====================================================================
# The suppression budget
# =====================================================================

def test_suppression_budget_caps_how_much_one_window_may_absorb(roster):
    """Following the patrol in buys one downgrade, not an evening of immunity."""
    profile = scenario_profile().model_copy(
        update={"max_suppressions_per_window": 3})
    roster.upsert(profile)
    m = PatrolMatcher(roster)

    decisions = []
    for _ in range(6):
        o = obs()
        a = m.assess(o)
        m.spend_budget(a, o)
        decisions.append(a.decision)

    assert decisions[:3] == [PatrolDecision.SUPPRESSED] * 3
    assert decisions[3:] == [PatrolDecision.DOWNGRADED] * 3
    assert m.metrics.budget_exhausted == 3
    # And nothing was lost: every event was still assessed and accounted for.
    assert m.metrics.raw_events == 6
    assert m.metrics.matched == 6


def test_budget_is_not_charged_for_an_assessment_that_was_not_acted_on(roster):
    """Inspecting an assessment must not consume a real patrol's allowance."""
    m = PatrolMatcher(roster)
    for _ in range(20):
        m.assess(obs())          # no spend_budget call
    assert m.budget_state() == {}
    assert m.assess(obs()).decision is PatrolDecision.SUPPRESSED


def test_budget_is_scoped_to_one_occurrence_of_the_window(roster):
    """Tonight's spent budget does not carry into tomorrow night."""
    profile = scenario_profile().model_copy(
        update={"max_suppressions_per_window": 1})
    roster.upsert(profile)
    m = PatrolMatcher(roster)

    first = obs()
    m.spend_budget(m.assess(first), first)
    assert m.assess(obs()).decision is PatrolDecision.DOWNGRADED

    tomorrow = obs(when=datetime(2026, 3, 18, 2, 30))
    assert m.assess(tomorrow).decision is PatrolDecision.SUPPRESSED


# =====================================================================
# Identity is used to withhold, never on its own to grant
# =====================================================================

def test_identity_is_ignored_unless_explicitly_configured(roster):
    """A plate read on a profile that did not opt in changes nothing."""
    m = PatrolMatcher(roster)
    assert not scenario_profile().identity_matching_enabled
    a = m.assess(obs(object_class=ObjectClass.PERSON, plate="XX00XX0000"))
    assert a.decision is PatrolDecision.SUPPRESSED


def test_a_mismatched_identifier_disqualifies_the_profile(roster):
    profile = scenario_profile().model_copy(update={
        "identity_matching_enabled": True,
        "vehicle_plates": ["WB24SSB001"],
        "object_classes": [ObjectClass.PERSON, ObjectClass.TRUCK],
    })
    roster.upsert(profile)
    m = PatrolMatcher(roster)

    right = m.assess(obs(object_class=ObjectClass.TRUCK, plate="WB 24 SSB 001"))
    assert right.decision is PatrolDecision.SUPPRESSED, right.reason

    wrong = m.assess(obs(object_class=ObjectClass.TRUCK, plate="WB24ZZ9999"))
    assert wrong.decision is PatrolDecision.NOT_MATCHED
    assert wrong.patrol_id is None


# =====================================================================
# Cues that cannot be computed must not count against a match
# =====================================================================

def test_a_camera_without_direction_analysis_can_still_match(roster):
    """A thermal unit with no ground plane reports no heading and no speed.

    That is a missing cue, not disagreement. Penalising it would make every
    patrol on such a camera permanently unmatchable.
    """
    m = PatrolMatcher(roster)
    a = m.assess(obs(heading_deg=None, speed_mps=None))
    assert a.decision is PatrolDecision.SUPPRESSED
    direction = next(c for c in a.cues if c.name == "direction")
    assert direction.evaluable is False


def test_route_trail_corroborates_but_is_not_required(roster):
    m = PatrolMatcher(roster)
    without = m.assess(obs())
    with_trail = m.assess(obs(route_trail=["CAM-011", "CAM-022", "CAM-014"]))
    assert without.decision is PatrolDecision.SUPPRESSED
    # A trail that walks the route backwards is real disagreement and shows up
    # in identity confidence, without being fatal on its own.
    assert with_trail.identity_confidence < without.identity_confidence


def test_route_trail_following_the_route_is_evaluable_and_agrees(roster):
    m = PatrolMatcher(roster)
    a = m.assess(obs(camera_id="CAM-011", zone_id="CAM-011-ZONE-1",
                     route_trail=["CAM-014", "CAM-022", "CAM-011"]))
    cue = next(c for c in a.cues if c.name == "route consistency")
    assert cue.evaluable is True
    assert cue.score == 1.0


# =====================================================================
# Metrics
# =====================================================================

def test_metrics_count_every_outcome_and_define_the_reduction():
    m = PatrolMetrics()
    for d in (PatrolDecision.SUPPRESSED, PatrolDecision.SUPPRESSED,
              PatrolDecision.DOWNGRADED, PatrolDecision.DEVIATION,
              PatrolDecision.NOT_MATCHED):
        m.record(d)
    d = m.as_dict()
    assert d["raw_events"] == 5
    assert d["patrol_matched_events"] == 4
    assert d["suppressed_events"] == 2
    assert d["downgraded_events"] == 1
    assert d["abnormal_patrol_events"] == 1
    assert d["unmatched_events"] == 1
    assert d["alert_reduction_percent"] == 40.0
    # The number must travel with the definition that bounds it.
    assert "not a benchmark" in d["definition"]


def test_metrics_are_zero_safe():
    assert PatrolMetrics().as_dict()["alert_reduction_percent"] == 0.0


# =====================================================================
# Roster persistence and the demo roster
# =====================================================================

def test_roster_round_trips_through_the_database(tmp_path):
    db = Database(tmp_path / "patrol.db")
    PatrolRoster(db).upsert(scenario_profile())

    reloaded = PatrolRoster(db)
    profile = reloaded.get(scenario_profile().patrol_id)
    assert profile is not None
    assert profile.cameras == ["CAM-014", "CAM-022", "CAM-011"]
    assert profile.windows[0].start_minute == 120

    assert reloaded.remove(profile.patrol_id)
    assert PatrolRoster(db).get(profile.patrol_id) is None


def test_demo_roster_opens_one_window_now_and_closes_another(tmp_path):
    """The demo needs a live suppression and a live downgrade at any hour."""
    now = datetime(2026, 3, 17, 14, 0)
    alpha, bravo = demo_patrols("BOP-EDGE-01", now)
    assert alpha.window_for(now)[1] == 0.0                 # open now
    gap = bravo.window_for(now)[1]
    assert 0 < gap <= bravo.windows[0].tolerance_minutes   # late, within tolerance


def test_reseeding_demo_profiles_preserves_an_operators_choice(tmp_path):
    db = Database(tmp_path / "patrol.db")
    r = PatrolRoster(db)
    seed_demo_patrols(r, "BOP-EDGE-01")
    r.set_active("PATROL-ALPHA", False)

    seed_demo_patrols(r, "BOP-EDGE-01")
    assert r.get("PATROL-ALPHA").active is False


def test_reseeding_never_overwrites_an_operator_entered_profile(tmp_path):
    db = Database(tmp_path / "patrol.db")
    r = PatrolRoster(db)
    r.upsert(PatrolProfile(patrol_id="PATROL-ALPHA", name="Operator's own",
                           cameras=["CAM-099"]))
    seed_demo_patrols(r, "BOP-EDGE-01")
    assert r.get("PATROL-ALPHA").name == "Operator's own"


# =====================================================================
# Integration with the rest of the node
# =====================================================================

def test_the_patrol_determination_is_sealed_into_the_hash_chain(matcher):
    """A decision not to interrupt a human must not be editable afterwards."""
    event = Event(
        event_id="EVT-PATROL-1", camera_id="CAM-014", node_id="BOP-EDGE-01",
        event_type=EventType.ZONE_INTRUSION, priority=Priority.LOW,
        priority_score=0.2, timestamp=datetime(2026, 3, 17, 2, 30, tzinfo=timezone.utc),
        patrol=matcher.assess(obs()),
    )
    sealed = compute_entry_hash(1, "", event)

    tampered = event.model_copy(deep=True)
    tampered.patrol.decision = PatrolDecision.NOT_MATCHED
    assert compute_entry_hash(1, "", tampered) != sealed


def test_an_event_without_a_patrol_assessment_hashes_as_it_always_did(matcher):
    """Adding the field must not invalidate chains written by earlier versions."""
    event = Event(
        event_id="EVT-PLAIN-1", camera_id="CAM-014", node_id="BOP-EDGE-01",
        event_type=EventType.ZONE_INTRUSION, priority=Priority.LOW,
        priority_score=0.2, timestamp=datetime(2026, 3, 17, 2, 30, tzinfo=timezone.utc),
    )
    assert event.patrol is None
    # The pre-v0.7 hash body, reproduced literally.
    import hashlib
    import json
    body = {
        "i": 1, "prev": "", "event_id": event.event_id, "node": event.node_id,
        "camera": event.camera_id, "type": event.event_type.value,
        "ts": event.timestamp.astimezone(timezone.utc).isoformat(),
        "mono_ns": event.monotonic_ns, "object": event.object_class.value,
        "track": event.track_id, "zone": event.zone_id,
        "score": round(event.priority_score, 4),
        "frame_sha256": None, "clip_sha256": None,
    }
    legacy = hashlib.sha256(json.dumps(
        body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    assert compute_entry_hash(1, "", event) == legacy


def test_a_suppressed_event_is_still_a_complete_recorded_event(matcher):
    """Suppression touches alerting only. The record stays whole."""
    assessment = matcher.assess(obs())
    event = Event(
        event_id="EVT-PATROL-2", camera_id="CAM-014", node_id="BOP-EDGE-01",
        event_type=EventType.ZONE_INTRUSION, priority=Priority.LOW,
        priority_score=0.11, patrol=assessment,
        alerted=False, alert_decision=assessment.reason,
    )
    restored = Event.model_validate_json(event.model_dump_json())
    assert restored.patrol is not None
    assert restored.patrol.decision is PatrolDecision.SUPPRESSED
    assert restored.patrol.reason
    assert restored.alerted is False
    # Nothing about the event's own record was removed.
    assert restored.event_id and restored.event_type is EventType.ZONE_INTRUSION


def test_utc_event_times_are_compared_against_local_patrol_windows(roster):
    """Schedules are written in local time; events are stamped in UTC.

    Constructed from the local wall clock the scenario window is written in, so
    this holds on any machine rather than only in one timezone.
    """
    m = PatrolMatcher(roster)
    local_naive = datetime(2026, 3, 17, 2, 30)
    aware = local_naive.astimezone()          # same instant, tagged local
    assert m.assess(obs(when=aware)).decision is PatrolDecision.SUPPRESSED

    # The same instant expressed in UTC must reach the same conclusion.
    as_utc = aware.astimezone(timezone.utc)
    assert m.assess(obs(when=as_utc)).decision is PatrolDecision.SUPPRESSED


def test_pipeline_accepts_the_matcher_as_a_constructor_argument():
    """Guards the integration bug that bit v0.5 and v0.6.

    Both times the wiring was added to the constructor body but not the
    signature, so the node raised TypeError on startup and the feature was
    silently absent. A signature check is cheap insurance.
    """
    import inspect

    from prahari.edge.pipeline import CameraPipeline
    params = inspect.signature(CameraPipeline.__init__).parameters
    assert "patrol_matcher" in params
    assert params["patrol_matcher"].default is None      # optional; off by default


def test_unconfigured_node_behaves_exactly_as_before():
    """With no matcher, the patrol layer must be completely inert."""
    from prahari.edge.pipeline import CameraPipeline

    assess = CameraPipeline._assess_patrol
    stub = type("Stub", (), {"patrol_matcher": None})()
    assert assess(stub, None, None, datetime.now(timezone.utc)) is None


def test_coordinator_trail_is_empty_for_an_unknown_track():
    from prahari.edge.crosscam.coordinator import CrossCameraCoordinator
    from prahari.edge.crosscam.topology import demo_topology

    coord = CrossCameraCoordinator(demo_topology(), "BOP-EDGE-01")
    assert coord.trail_for("CAM-014", 999) == []


def test_coordinator_trail_reports_the_camera_sequence():
    from prahari.edge.crosscam.coordinator import CrossCameraCoordinator
    from prahari.edge.crosscam.topology import demo_topology

    coord = CrossCameraCoordinator(demo_topology(), "BOP-EDGE-01")
    t0 = datetime(2026, 3, 17, 2, 30, tzinfo=timezone.utc)
    coord.on_track_confirmed("CAM-014", 1, "person", None, t0)
    coord.on_track_lost("CAM-014", 1, t0 + timedelta(seconds=10))
    coord.on_track_confirmed("CAM-022", 7, "person", None,
                             t0 + timedelta(seconds=45))
    trail = coord.trail_for("CAM-022", 7)
    assert trail[-1] == "CAM-022"
    if len(trail) > 1:                    # a handoff was accepted
        assert trail == ["CAM-014", "CAM-022"]
