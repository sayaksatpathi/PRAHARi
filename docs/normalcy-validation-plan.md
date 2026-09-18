# Next Validation Task — Learned NormalcyModel (pattern-of-life)

**Why this is next.** The Border Scenario evaluation
([border-scenario-evaluation.md](border-scenario-evaluation.md)) drives the
harness path, which **omits the learned pattern-of-life layer**. So its
`NORMAL_OPEN_BORDER` result is explicitly a **pre-normalcy upper bound**: raw soft
rules fire on lawful ambient traffic because nothing has yet learned that this
traffic is routine. The system's strongest claim — *lawful open-border movement
does not become an intrusion alert* — is carried by `NormalcyModel`
(`prahari/edge/normalcy.py`) and must be validated on its own before the SIH
demo/release evidence is frozen.

Sequence: **visual clip check ✅ → normalcy-specific validation (this task) →
freeze SIH demo/release evidence.**

## Objective

Quantify what the learned `NormalcyModel` does to the `NORMAL_OPEN_BORDER`
false-alarm rate, and confirm it suppresses *routine* lawful traffic while
*retaining* genuine anomalies — separately from the pre-normalcy number.

## Constraints (carry over from this milestone)

- **No pipeline or model changes.** `NormalcyModel` is used as-is (`observe`,
  `seed_baseline`, `evaluate`, `decay`). Validation is *additive tooling only*.
- **No retraining, no dataset download, SCRFD stays CANDIDATE.**
- **SIMULATED/DEMO labeling** on every result; never real-world border performance.

## Method (two complementary runs)

1. **Seeded-baseline run (fast, demo affordance).**
   `NormalcyModel.seed_baseline(camera_id, ObjectClass.PERSON, busy_hours=...)`
   preloads a plausible daily rhythm for CAM-022 (open border), so routine hours
   are "expected". Then re-run `NORMAL_OPEN_BORDER` (and the anomaly scenarios)
   with `normalcy.evaluate(...)` applied **before** the governor, exactly as
   `prahari/edge/pipeline.py::_assess` does. Record the verdict ratio per event.

2. **Learned-baseline run (longer traffic, no seeding).**
   Generate a longer ambient-only sequence (e.g. 20–60 min of simulated open-border
   traffic) and feed it through `normalcy.observe(...)` so the model *learns* the
   pattern from observation rather than a seed. Then evaluate the same scenarios.
   This is the honest counterpart to the seeded affordance and shows the model
   works from real observation, not just a preloaded rhythm.

In both runs, apply the pipeline's own ordering: an event judged **routine** by
normalcy on the open-border doctrine is suppressed/downgraded before the governor;
hard tripwire events (`line_crossing`, `zone_intrusion`) are *not* suppressed by
normalcy — crossing the fence is the event regardless of pattern.

## Metrics to record (measured, not asserted)

| Metric | Definition | Expected direction |
|--------|-----------|--------------------|
| False-alarm rate — NORMAL | alerts on lawful traffic per hour, **with** normalcy | ↓ vs pre-normalcy upper bound |
| Routine suppression % | routine soft events suppressed / raised | high |
| Anomaly retention | OFF_ROUTE / NIGHT / SUSPICIOUS still alert **with** normalcy on | unchanged (no regression) |
| Normalcy ratio separation | mean ratio(routine) vs ratio(anomalous) | clear gap |
| Seeded vs learned agreement | do both runs agree on routine/unusual | consistent |

Success = the NORMAL false-alarm rate drops materially **and** every anomaly
scenario still alerts (no true-positive regression), with seeded and learned
baselines agreeing.

## Deliverables

- `scripts/validate_normalcy.py` — additive runner (reuses `NormalcyModel` +
  the border-scenario harness; no pipeline edits).
- `var/normalcy_validation.json` — measured results.
- `docs/normalcy-validation.md` — write-up, SIMULATED/DEMO labeled.
- Update the `NORMAL_OPEN_BORDER` row context in
  [border-scenario-evaluation.md](border-scenario-evaluation.md) and the
  [benchmark-matrix.md](benchmark-matrix.md) with the *with-normalcy* result
  **beside** (not replacing) the pre-normalcy upper bound.

## Then: freeze

Once the with-normalcy result is measured and recorded, the SIH demo/release
evidence package is complete and can be frozen:
detection benchmarks · face candidate comparison · border scenario E2E ·
normalcy validation · provenance/licensing gates.

## API reference (already present, unchanged)

- `NormalcyModel(db)` · `.seed_baseline(camera_id, object_class, busy_hours, ...)`
- `.observe(camera_id, object_class, when, zone_id="")`
- `.evaluate(camera_id, object_class, when, zone_id="") -> NormalcyVerdict`
  (`.ratio`, `.is_routine`, `.is_unusual`, `.samples`)
- `.decay(days_elapsed)` — ages counts so the model tracks change.
