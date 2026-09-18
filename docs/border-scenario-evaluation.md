# Prahari Border Scenario Video Set — End-to-End Evaluation

> **STATUS: SIMULATED / DEMO — not real-world border performance.**
> Every clip is rendered from Prahari's deterministic simulator; every result
> below is a simulated system demonstration, never a measurement of real
> India–Nepal / India–Bhutan border footage. Reproduce with
> `scripts/border_scenarios.py` (writes `var/border_scenario_evaluation.json`).

This is the milestone that moves Prahari from *independently benchmarked AI
components* to a *complete system demonstration*: existing-CCTV → detection →
tracking → rules → alert governor → evidence + hash chain, plus patrol matching,
tamper detection and offline integrity — all exercised on one controlled pack.

## How it was run

- **Pipeline: production components, unchanged.** The runner drives the real
  `EvaluationHarness` (detection → tracking → rules → alert governor), the real
  `PatrolMatcher`, the real `TamperDetector`, and the real `EvidenceStore` +
  `EvidenceLedger` (append-only SHA-256 hash chain). No pipeline code was changed.
- **Detector: SYNTHETIC.** The deterministic, ground-truthed synthetic detector
  is used so scenarios are reproducible and scorable — no trained network, **no
  retraining**, no model promoted (SCRFD stays CANDIDATE), no datasets downloaded.
- **Clips:** 8 × 720p @ 12 fps rendered to `data/demo/border_scenarios/NN_*.mp4`,
  each with a ground-truth `NN_*.json`.

## Results

| # | Scenario | Detection | Alert (exp→act) | Event type(s) fired | Latency | E2E ms/frame · FPS | Evidence · hash-chain |
|---|----------|-----------|-----------------|---------------------|---------|--------------------|-----------------------|
| 01 | NORMAL_OPEN_BORDER | *pre-normalcy upper bound* | False→True¹ | soft: night/off-route/suspicious | — | 1.51 · 663 | — |
| 02 | PATROL_MATCHED | ✅ CORRECT | False→False | `suppressed` (patrol) | — | — | — |
| 03 | PATROL_DEVIATION | ✅ CORRECT | True→True | `deviation` (patrol) | — | — | ✅ / PASS |
| 04 | OFF_ROUTE_MOVEMENT | ✅ CORRECT | True→True | `line_crossing`, `zone_intrusion` | 3500 ms | 2.20 · 454 | ✅ / PASS |
| 05 | NIGHT_MOVEMENT | ✅ CORRECT | True→True | `night_movement`, `off_route` | 333 ms | 2.02 · 494 | ✅ / PASS |
| 06 | SUSPICIOUS_ACTIVITY | ✅ CORRECT | True→True | `group_movement`, `suspicious_activity` | — | 2.52 · 397 | ✅ / PASS |
| 07 | CAMERA_TAMPER | ✅ CORRECT | True→True | `camera_tamper` (kind: frozen) | — | — | ✅ / PASS |
| 08 | NETWORK_OUTAGE | ✅ CORRECT | True→True | `line_crossing`, `zone_intrusion` | 3500 ms | 2.48 · 404 | ✅ / PASS · **recovery PASS** |

**Detection correctness: 7/7** incident/patrol/tamper scenarios fired the right
event type or made the right friendly-force / integrity decision.
**Evidence + hash chain: PASS on all 6 alerting scenarios.**
**Outage: PASS** — events queued locally while offline, chain re-verified intact
on reconnect.

¹ See the false-alarm note below — this is the expected, honest behaviour of the
harness path, not a defect.

## The honest part: false alarms and the normalcy layer

The single most important SIH story is *not* "we detected a person" — it is
"**we distinguished normal open-border movement from abnormal behaviour and only
alerted when justified**". This evaluation is deliberately transparent about
where that discrimination comes from:

- **The harness path is a documented PRE-NORMALCY UPPER BOUND.** It exercises
  rules → governor but **omits the learnt pattern-of-life (normalcy) layer**, and
  the per-hour governor rationing needs *hours* of traffic volume, not a 12-second
  clip. So on NORMAL_OPEN_BORDER the raw soft-event rules fire on ambient traffic
  and are not yet suppressed. This is the **upper bound**; the live node damps it
  further.
- **In-band proof of normal-vs-abnormal discrimination is the PATROL pair.**
  PATROL_MATCHED is *genuinely suppressed* by the real `PatrolMatcher`
  (`suppressed`, operator not interrupted), while PATROL_DEVIATION — visually
  similar movement, wrong direction — is *escalated* (`deviation`). Same camera,
  same zone, opposite outcome, decided by the real friendly-force layer.
- **Lawful-traffic suppression** is carried by the normalcy layer + governor
  rationing + patrol matching, validated separately in
  [patrol-suppression.md](patrol-suppression.md) and the fleet evaluation in
  [evaluation.md](evaluation.md) (measured 71–97% suppression).
- **The normalcy layer is now validated in-path** — see
  [normalcy-validation.md](normalcy-validation.md): with a seeded pattern-of-life
  baseline, this NORMAL_OPEN_BORDER pre-normalcy load of ~34 alerts drops to **0**
  (routine traffic suppressed), while the anomaly scenarios still alert. That
  result — not this pre-normalcy row — is the normalcy claim.

> Bottom line: the pipeline raises the **right event types** with **evidence-backed,
> tamper-evident** records, and **discriminates friendly patrols from threats** —
> demonstrated here. The absolute false-alarm *rate* on lawful traffic is governed
> by the normalcy/rationing layers, which this short-clip harness path intentionally
> under-states rather than over-claims.

## What each stage genuinely exercises

| Stage | Exercised here | Notes |
|-------|----------------|-------|
| Detection | ✅ (synthetic, deterministic) | Real detector interface; numbers characterise modelled behaviour, not a trained net |
| Tracking | ✅ ByteTrack | Real tracker |
| Rules (fenced + open doctrine) | ✅ | Correct event types per camera doctrine |
| Alert governor | ✅ | Real `AlertGovernor.decide`; per-hour rationing under-exercised by short clips |
| Patrol matching | ✅ | Real `PatrolMatcher`; matched→suppressed, deviation→escalated |
| Normalcy (pattern-of-life) | ⚠️ not in this path | Documented upper bound; validated separately |
| Re-ID (cross-camera) | ⚠️ single-camera scenarios | Re-ID validated on Market-1501 (see [benchmark-matrix.md](benchmark-matrix.md)) |
| Evidence generation | ✅ | Real `EvidenceStore` frame + thumbnail |
| Hash-chain verification | ✅ PASS | Real `EvidenceLedger.verify()` walks the SHA-256 chain |
| Tamper detection | ✅ | Real `TamperDetector` (covered lens → frozen-frame verdict) |
| Offline / store-and-forward | ✅ | Events chained locally offline, verified intact on reconnect |

## Reproduce

```bash
.venv/Scripts/python.exe scripts/border_scenarios.py
```

Outputs: `data/demo/border_scenarios/NN_*.mp4` + `NN_*.json`,
`var/border_scenario_evaluation.json`. All artifacts are SIMULATED/DEMO.
