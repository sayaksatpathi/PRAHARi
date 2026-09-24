# NormalcyModel Validation — Pattern-of-Life in the Decision Path

> **STATUS: SIMULATED.** These cases exercise the **real `NormalcyModel`** inside
> the rule→governor decision path — *not* the harness's pre-normalcy upper bound.
> The simulator provides the scenario structure (routine vs anomaly, patrol,
> night) that generic real footage does not, so every case here is SIMULATED.
> Reproduce with `scripts/validate_normalcy.py` → `var/normalcy_validation.json`.

This closes the gap called out in
[border-scenario-evaluation.md](border-scenario-evaluation.md): there,
`NORMAL_OPEN_BORDER` was only a **pre-normalcy upper bound**. Here the learned
pattern-of-life layer is put in the loop and measured.

## Setup

- **NormalcyModel used as-is** (`prahari/edge/normalcy.py`) — `seed_baseline`,
  `evaluate`, thresholds `is_routine` (ratio ≤ 0.6) / `is_unusual` (ratio ≥ 2.0),
  both needing ≥ 5 samples. No model retrained or replaced; production detector
  and config unchanged (synthetic detector used only for deterministic,
  ground-truthed simulation, exactly as the harness does).
- **Baseline: the existing demo-seed mechanism** (`NormalcyModel.seed_baseline`),
  a day-busy rhythm (06–18 busy) seeded for every routinely-observed class. This
  is a demo affordance — a real node learns it from weeks of its own traffic.
- **Decision path:** a routine *soft* (pattern-of-life) event is suppressed
  before the governor; an unusual one is kept/escalated; fenced-doctrine hard
  tripwires (`line_crossing`, `zone_intrusion`, `wrong_direction`) are **never**
  normalcy-suppressed — crossing the line is the event regardless of pattern.
- **Isolation:** each harness case runs in its own subprocess (sequential
  in-process harness runs leak simulator/tracker state and zero later runs — see
  Limitations).

## Results (measured)

| Case | Scenario | Event time | Normalcy class (ratio) | Alerts | Suppressed | Escalated | Decision lat | E2E ms/frame |
|------|----------|-----------|------------------------|--------|-----------|-----------|--------------|--------------|
| A | NORMAL_OPEN_BORDER | 14:00 day | **routine** (0.51) | **34→0** | 56 | 0 | 0.10 ms | 1.49 |
| E | NIGHT_MOVEMENT | 02:00 night | **unusual** (20.5) | 36 | 0 | 78 | 0.12 ms | 1.45 |
| D | OFF_ROUTE_MOVEMENT | 14:00 day | routine-by-time (0.51) | 28 | 8 | 0 | 0.13 ms | 1.22 |
| B | PATROL_MATCHED | 02:30 night | (patrol overrides) | — | suppressed | — | — | — |
| C | PATROL_DEVIATION | 02:30 night | (patrol overrides) | — | — | escalated | — | — |

### Headline

**NORMAL_OPEN_BORDER false-alert load: 34 (pre-normalcy) → 0 (with normalcy)** —
56 routine soft events suppressed. Every anomaly case still alerts: **no
true-positive regression.**

### Case-by-case (expected vs actual)

- **A — NORMAL_OPEN_BORDER** · expected NORMAL → **actual routine (ratio 0.51).**
  Lawful daytime open-border traffic is classified routine and all 56 soft
  events are suppressed before the governor: 34 pre-normalcy alerts → **0**. This
  is the system's core claim, now measured rather than asserted.
- **E — NIGHT_MOVEMENT** · expected UNUSUAL → **actual unusual (ratio 20.5).**
  Movement at a quiet hour is classified unusual; nothing is suppressed, 78
  events escalate, 36 alert. Correct doctrine — movement at 02:00 on a border is
  inherently notable.
- **D — OFF_ROUTE_MOVEMENT** · expected UNUSUAL by route → **temporal view
  routine (0.51), 28 alerts retained.** The hour is routine, so normalcy would
  not raise this on time alone — but on the fenced camera the hard tripwires fire
  regardless, so the off-route entrant still alerts (only 8 incidental soft
  events suppressed). This is the honest boundary of the model: **normalcy is a
  temporal prior, not a route judge** — route/zone rules and the patrol layer
  carry that.
- **B / C — PATROL** · the real `PatrolMatcher` decides: a declared patrol on its
  route/window is **suppressed** (temporally unusual at night, but friendly),
  while a deviation is **escalated**. Friendly-force and normalcy are
  complementary layers.

**Decision latency** for the normalcy lookup is ~0.1 ms/event; end-to-end frame
cost is ~1.2–1.5 ms/frame. Adding pattern-of-life to the path is effectively free.

## Limitations (short clip vs long-term baseline)

- **The baseline is SEEDED, not learned.** `seed_baseline` is a demo affordance;
  it preloads a plausible day/night rhythm so the model has something to reason
  against without a week of wall-clock learning. A production node learns its own
  per-camera, per-zone rhythm from `NormalcyModel.observe` + `decay` over weeks.
  **Measuring that requires long-term field data, which is out of scope here and
  was not downloaded.** So the *absolute* alert rates are not a long-term field
  measurement — what is validated is that the model, once it holds a pattern of
  life, **classifies routine vs unusual correctly and suppresses/keeps
  accordingly in the decision path.**
- **Short clips (~12 s).** Per-hour governor rationing and rate anomalies (the
  10-minute-window term in `evaluate`) are under-exercised by short clips; the
  suppression measured here is the pattern-of-life (hour-bucket) term.
- **Only seeded classes are suppressible.** A class with no learnt baseline is
  (correctly) never called routine; this is why all routinely-observed classes
  are seeded.
- **SIMULATED only.** Normalcy needs scenario structure and a per-camera baseline;
  the generic real-footage clips (see [DATA_LICENSES.md](../DATA_LICENSES.md)) are
  for real *detection* testing, not normalcy. A real-footage normalcy validation
  would require a fixed deployment camera observed over weeks.

## Learned baseline (observe path, not seeded) — addresses the "configured, not learned" critique

The result above uses `seed_baseline` (a demo affordance). `scripts/validate_normalcy_learned.py`
instead builds the pattern the way a live node does — by calling the production
`NormalcyModel.observe()` over a stream of observations following a realistic
weekly traffic profile (busy daytime plateau, quiet night) — and confirms the
model then classifies correctly:

- **Learned from 3,598 observations** → **day 14:00 = routine (ratio 0.54)**,
  **night 02:00 = unusual (ratio 21.4)** → **PASS**.

This validates the **learning mechanism** (observe → counts → classification),
i.e. the seed was only a shortcut for the same path. **Honest limit:** the
*observations* are a realistic *simulated* traffic profile — the simulator does
not model real multi-week time-of-day traffic — so this is not a field baseline.
A genuine field baseline still requires weeks of a real camera's own traffic,
which is the #1 open gap (`var/normalcy_learned_validation.json`).

## Reproduce

```bash
.venv/Scripts/python.exe scripts/validate_normalcy.py           # seeded
.venv/Scripts/python.exe scripts/validate_normalcy_learned.py   # learned via observe()
```
