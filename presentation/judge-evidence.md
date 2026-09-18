# Prahari — Judge-Facing Evidence (SIH 2026)

One sheet of measured figures, each clearly labelled, with the caveats kept
visible. Full reconciliation: [`../docs/final-sih-release-readiness.md`](../docs/final-sih-release-readiness.md).

## Measured results

| Capability | Figure | Kind |
|-----------|--------|------|
| **Normalcy** | `34 → 0` routine alerts — **56 routine events suppressed** | SIMULATED |
| **Re-ID (Market-1501)** | `Rank-1 0.7150`, `mAP 0.4925` (retrain); `0.705 / 0.485` production | MEASURED |
| **Face — Haar (default)** | `AP@0.5 0.121` (WIDER FACE val) | MEASURED |
| **Face — SCRFD (candidate)** | `AP@0.5 0.489` (WIDER FACE val, CPU) | MEASURED · CANDIDATE |
| **Border scenarios** | `7/7 detection-correct` (+ 6/6 evidence hash-chain PASS) | SIMULATED |
| **Detector (MOT17)** | `12.6 fps`, `MOTA 0.403`, `IDF1 0.500` | MEASURED |
| **Real-footage detection** | `2,898 person` detections, ground-level street clip | MEASURED (REAL-FOOTAGE) |

## Caveats (keep visible)

```
SIMULATED ≠ FIELD VALIDATED
SCRFD = CANDIDATE  (research-only weights, not promoted)
ONVIF = UNVALIDATED
WIDER FACE TEST GT = WITHHELD  (val-split figures only)
No SCRFD GPU benchmark  (CPU figures only)
Street Scene = NOT DOWNLOADED
Seeded normalcy baseline ≠ long-term field baseline
```

**Release classification: SIH DEMO READY / EVIDENCE FROZEN — not production-field validated.**
