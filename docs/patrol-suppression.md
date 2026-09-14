# Friendly-force (patrol) suppression

## The problem

A border outpost runs its own patrols. They walk the fence line, on a schedule,
through the same zones the rules watch, in the same direction, every night.

So the most reliable, most repeatable, most perfectly-detected event a border
video system produces is **its own side walking past**. On a fenced sector that
is routinely the largest single category of false alarm, and — worse than its
volume — it is *predictable*. It arrives at 02:00. It arrives again at 02:04. It
arrives every night.

That is precisely the pattern that teaches an operator the alarm means nothing.
By night three the 02:00 alert is dismissed without being looked at, and on the
night something else crosses at 02:00, it is dismissed too. The system did not
fail by missing it. It failed by having spent its credibility.

Prahari already rations alerts by score ([alerting.py](../prahari/edge/alerting.py))
and damps what operators mark as false alarms. Neither helps here. The governor
rations by *significance*, and a person in a restricted zone is significant. The
feedback loop damps a camera and event type wholesale, which would damp the real
intrusion along with the patrol. Patrol movement needs to be recognised for what
it is, not scored down for being frequent.

## Why this is not a whitelist

The obvious fix is a whitelist: mute CAM-014 between 02:00 and 03:00.

That is not a fix, it is a scheduled hole in the perimeter. Anyone who learns
the patrol schedule — and a patrol schedule is learnable by standing on a hill
with a watch for a week — inherits the exemption. It is the same failure as a
guard who waves through anyone in the right uniform.

A whitelist has two states: known and unknown. Everything resembling the known
thing gets its immunity. This module has four, and the extra two are the point:

| | |
|---|---|
| **SUPPRESSED** | Conforms to a declared patrol on **every** cue that could be checked. Recorded and sealed in full; the operator is not interrupted. |
| **DOWNGRADED** | Probably the patrol, not certainly. Kept as a lower-priority alert. |
| **DEVIATION** | Confidently *is* the patrol, and is visibly **not behaving like it**. **Escalated** above an ordinary alert. |
| **NOT_MATCHED** | No declared patrol accounts for this. Normal pipeline, untouched. |

`DEVIATION` is the state a whitelist cannot express. To a whitelist, a patrol
walking its route backwards at 03:00 is still the patrol, and is still muted.
Here it is the loudest thing on the board — because a patrol reversing its route,
leaving it, or sprinting down it is either lost, in trouble, or not the patrol,
and all three deserve more attention than an ordinary alert, not less.

Four further properties make the difference structural rather than rhetorical:

**Patrols are declared, never inferred.** A profile exists because somebody with
authority at the post wrote it down, and every change goes through the audit log.
The node does **not** learn which nightly movement is friendly and quietly stop
reporting it — a system that learns to ignore whatever it sees often enough will
eventually learn to ignore an established smuggling route.

**Schedule is a hard gate, not a cue among several.** Past the tolerance band the
profile stops being a candidate outright. A stranger walking the patrol route in
the patrol's direction looks *identical* to the patrol; every other cue agreeing
is not evidence, and this layer is not permitted to act on it.

**Suppression is budgeted per window.** A patrol is a handful of transits, not an
open licence. Once a profile has spent its budget the remaining look-alikes take
the normal path — so walking in behind the patrol buys an intruder one downgrade,
not an evening of immunity.

**Nothing is ever deleted, hidden, or left unrecorded.** A suppressed event is
written, scored, given evidence, sealed into the hash chain *with its assessment
attached*, and synchronised. It simply did not ring a bell.

## Architecture

```
 detection → tracking → rules → scoring ─┬─► PATROL ASSESSMENT ──► segmentation
                                         │         │                    │
                                         │         ▼                    ▼
                                         │   suppressed? ──yes──► alerted = false
                                         │         │                    │
                                         │         no                   │
                                         │         ▼                    ▼
                                         └──► alert governor ──► evidence → ledger → sync
```

Three placement decisions:

**Between scoring and segmentation.** An event a declared patrol accounts for
should not be spending the node's one expensive second-stage pass, and an event
escalated as a deviation should be more likely to get one.

**Before the alert governor, and bypassing it when suppressed.** The governor
rations *candidate* alerts against an operator's attention budget. Movement a
patrol accounts for was never a candidate, and routing it through the governor
would let a routine patrol consume budget a real alert then could not get.

**Node-wide, not per camera.** A patrol crosses cameras, so its route, its
schedule and its suppression budget have to be shared across them. Camera
pipelines run in executor threads and all call the one matcher, so every mutating
entry point is lock-guarded.

| Module | Role |
|---|---|
| [`common/models.py`](../prahari/common/models.py) | `PatrolProfile`, `PatrolWindow`, `PatrolAssessment`, `PatrolDecision` |
| [`patrol/roster.py`](../prahari/edge/patrol/roster.py) | The declared roster; SQLite-backed, auditable, revocable |
| [`patrol/matcher.py`](../prahari/edge/patrol/matcher.py) | Cue scoring, the decision policy, budgets, metrics |
| [`patrol/scenarios.py`](../prahari/edge/patrol/scenarios.py) | The five reference scenarios, as data |

## A patrol profile

Who, where, when, which way:

```python
PatrolProfile(
    patrol_id="PATROL-ALPHA",
    name="Alpha — North Fence Foot Patrol",
    cameras=["CAM-014", "CAM-022", "CAM-011"],       # the route, in order
    zones=["CAM-014-ZONE-1", "CAM-022-ROUTE-1"],     # where it may be
    windows=[PatrolWindow(start_minute=2*60, end_minute=3*60,
                          tolerance_minutes=30, days=[0,1,2,3,4])],
    expected_heading_deg=270.0, heading_tolerance_deg=70.0,
    object_classes=[ObjectClass.PERSON],
    max_group_size=4, min_speed_mps=0.4, max_speed_mps=2.2,
    identity_matching_enabled=False,                 # opt-in, per profile
    max_suppressions_per_window=6,
    active=True,
)
```

Windows are minutes since **local** midnight, so a duty officer can read and edit
one without a timezone argument; events are stamped in UTC and converted once, at
the comparison. A window whose end precedes its start spans midnight — the common
case for a night patrol — and is budgeted against the day it began.

## Matching logic

Two gates, then five weighted cues, then a policy.

**Gates** (fail → the profile is not a candidate at all):

- the camera is on the profile's route;
- the object class is one the patrol can be;
- the observation is within the window's tolerance (see above);
- if identity matching is enabled and an identifier was read, it is one of the
  patrol's.

**Cues**, each scored 0–1:

| Cue | Weight | What it asks |
|---|---|---|
| Schedule | 0.34 | Inside the window, or how far outside? |
| Route zone | 0.22 | Is this a zone the patrol is routed through? |
| Direction | 0.22 | Heading within tolerance of the expected one? |
| Route consistency | 0.12 | Did the cross-camera trail arrive the way the route would bring it? |
| Movement | 0.10 | Group strength and pace plausible for this patrol? |

A cue that **cannot be computed is not counted** — the weighted mean runs over
evaluable cues only. This matters: a thermal unit with no ground plane reports no
heading and no speed. That is a missing cue, not disagreement, and penalising it
would make every patrol on such a camera permanently unmatchable.

The cues then answer **two separate questions**, kept separate on purpose:

```
identity_confidence = schedule (0.60) + route consistency (0.40)   is this the patrol?
conformance         = direction (0.55) + zone (0.30) + movement (0.15)   is it behaving like it?
match_score         = 0.5 × identity + 0.5 × conformance
```

Collapsing those into one number is exactly what makes a system unable to tell a
routine patrol from a patrol in trouble.

**Policy**, applied in order:

1. `identity ≥ 0.70` **and** a hard violation present → **DEVIATION** (+0.16).
   Hard violations: heading ≥ 90° from expected *and* outside tolerance; a zone
   the patrol is not routed through on a camera where it declares zones; a group
   larger than the patrol's strength; speed above twice its maximum.
2. `match ≥ 0.72` → **SUPPRESSED** (−0.35) — unless the window's budget is spent,
   in which case **DOWNGRADED** with that stated as the reason.
3. `match ≥ 0.45` → **DOWNGRADED** (−0.16).
4. otherwise → **NOT_MATCHED** (no adjustment).

Off schedule, `match` is capped at **0.65** — below the suppression bar — so a
late patrol can never be suppressed however well everything else agrees.

The adjustment is contributed as a named `PriorityFactor`, so it appears in the
alert card's arithmetic alongside every other contribution rather than moving the
score invisibly.

### Identity is used to withhold, never on its own to grant

Identity matching is **off by default and opt-in per profile**, and only ever
*subtracts*: a plate that was read and is not the patrol's disqualifies the
profile. A matching plate is not what earns a suppression — the conformance test
still has to pass on its own. "The system stopped alerting because it thought it
recognised someone" is the failure mode that makes a video system inadmissible.

Plate reading is the only identity signal available, and only where the
capability certificate grants ANPR. Face recognition is never granted on any
camera at any measured quality — see [privacy.md](privacy.md).

## The five scenarios

Declared once in `patrol/scenarios.py` and consumed three times: by the unit
tests, by `scripts/verify_patrol.py`, and by `/api/patrols/scenarios`, which the
dashboard renders. Every consumer runs the same real `PatrolMatcher`. If the
policy changes, they stop passing.

| | Scenario | Decided | Effect |
|---|---|---|---|
| **A** | Patrol on its route, in its window, right direction | `suppressed` (match 1.00) | Recorded and sealed in full; operator not interrupted |
| **B** | Same movement, 20 min after the window closed | `downgraded` (0.65) | Kept as a lower-priority alert |
| **C** | On route, in window — walking the wrong way | `deviation` (id 1.00, conformance 0.45) | **Escalated** |
| **D** | In window, right direction — in a zone it is not routed through | `deviation` (id 1.00, conformance 0.70) | **Escalated** |
| **E** | Same camera, zone, direction and pace as A — at 21:00 | `not_matched` (0.00) | Normal alert pipeline, untouched |

A and E are the pair that matters. They are **visually indistinguishable**: the
same person, the same zone, the same heading, the same unhurried pace. Only the
clock differs, and the clock alone flips the outcome from "do not interrupt
anybody" to "normal alert". That is the whole argument for schedule being a gate.

```bash
.venv/Scripts/python.exe scripts/verify_patrol.py
```

## Metrics

`/api/patrols/metrics` reports counters for the current run: events assessed,
patrol-matched, suppressed, downgraded, abnormal (deviations), unmatched, and
budget exhaustions.

`alert_reduction_percent` is defined narrowly and travels with its definition in
the API response: **the share of assessed events this node removed from the alert
path because a declared patrol accounted for them.** It is a count of this run. It
is *not* a false-alarm-rate improvement — that would need annotated ground truth
this build does not have, and no such number is claimed anywhere.

## Observed on the demo fleet

One run of the demonstration node, five simulated cameras, two declared patrol
profiles, with the patrol injected on its route three times and once in reverse:

| | |
|---|---|
| Events assessed | 375 |
| Patrol-matched | 166 (118 downgraded, 42 deviations, 6 suppressed) |
| Unmatched — normal pipeline | 209 |
| Removed from the alert path | **1.6 %** |
| Suppression budget exhausted | 82 times |
| Hash chain | 332 entries, verifies with the patrol determinations sealed in |

Two of those numbers need saying plainly rather than dressing up.

**1.6 % is low**, and it is the honest figure for this run. The demo simulator
generates continuous ambient foot traffic, most of which never resembles a
patrol, so the denominator is large. It is not a claim about a real deployment,
where a patrol's transits are a much larger share of a much smaller event count.

**The budget exhausted 82 times**, after only 6 suppressions. That is the
safeguard working exactly as designed — ambient pedestrians on the demo camera
*do* conform to the profile on every cue, because they are people walking through
the right zone in the right direction inside the window, and the budget is what
stops the profile absorbing all of them. Without it, the 82 would have been 82
free passes.

It is also a **configuration signal**. A profile that repeatedly exhausts its
budget is absorbing far more movement than one patrol's worth, which means the
declaration is too broad — a window too long, a zone too large, a route too
permissive. The counter is exposed at `/api/patrols/metrics` for exactly that
reason. Tightening the demo window from 75 minutes to a realistic 26-minute
transit was the first thing this counter caught.

The deviation path was confirmed live: the reverse-walking patrol produced a
`line_crossing` scored **0.94** that alerted, against the same movement inbound
scoring 0.22-0.25 and not alerting.

## Demonstration

Both buttons inject the same two-man foot patrol into the simulated scene; only
the direction differs. Whether the result is suppressed or escalated is decided
by the matcher from the real track, not by the button.

```bash
curl -X POST localhost:8420/api/demo/action \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"patrol","camera_id":"CAM-014"}'
```

The demo profiles' windows are generated **relative to the clock at node start** —
ALPHA's is open now, BRAVO's closed a short while ago — because a demo is run at
whatever hour it is run, and a patrol layer that can only be shown working at
02:00 cannot be shown working. They are marked `metadata.demo` and are refreshed
on every start; profiles an operator entered are never touched.

## Limitations

- **A patrol is only as good as its declaration.** A route that has changed, or a
  schedule nobody updated, produces deviations for real patrols and no
  suppression at all. This layer makes the declaration operationally load-bearing.
- **It cannot tell two people apart.** An infiltrator moving with the patrol, at
  patrol pace, in the patrol's direction, inside the window, is inside every cue
  this layer has. The window budget bounds the damage — they get one downgrade,
  not an evening — but it does not detect them. Genuine separation needs
  re-identification or a positive-identification channel neither of which exists
  here. This is a real gap, not a tuning problem.
- **Thresholds are policy, not calibration.** 0.72, 0.45, 0.65, 0.70 and the cue
  weights are argued-for settings with visible arithmetic. Nothing here has been
  fitted to border footage, and none of it is presented as a probability.
- **Route consistency needs the cross-camera trail**, which most single-camera
  events do not have. It is corroboration when present, never a requirement.
- **Direction needs a usable heading.** A camera without direction analysis
  simply drops that cue, so its patrols are matched on schedule, zone and
  movement — a weaker test, and the assessment says so.
- **Budgets are in memory** and reset when the node restarts. The events and
  their assessments are persisted and sealed; the spend counter is not.
- **One roster per node.** There is no sector-level patrol registry, so a patrol
  crossing between nodes is two declarations.
- **Schedules are static.** There is no ingestion from a duty-roster system, and
  no handling of an ad-hoc patrol called out at short notice — that movement will
  correctly get the normal alert path, which is the safe failure but not a
  convenient one.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/patrols` | The roster, its metrics and the policy thresholds |
| POST | `/api/patrols` | Create or update a profile (admin; audited) |
| POST | `/api/patrols/{id}/active` | Activate or revoke, without deleting the record (admin; audited) |
| DELETE | `/api/patrols/{id}` | Remove a profile (admin; audited) |
| GET | `/api/patrols/metrics` | Run counters and live budget state |
| GET | `/api/patrols/scenarios` | The five scenarios, evaluated live by the real matcher |

Every event carries its assessment at `event.patrol`, and the decision is
committed to in the hash chain — added conditionally, so events with no
assessment hash exactly as they did before this milestone and chains written by
earlier versions keep verifying after an upgrade.
