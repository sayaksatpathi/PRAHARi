# Prahari — Judge-Facing Evidence (SIH 2026 · Blockchain & Cybersecurity)

One sheet of measured figures, each labelled by kind, caveats kept visible. Theme
order: **security & integrity first**, AI application second. Full reconciliation:
[`../docs/judge-critique-response.md`](../docs/judge-critique-response.md),
[`../docs/threat-model.md`](../docs/threat-model.md).

## Security & evidence integrity (the theme)

| Control | Evidence | Status |
|---|---|---|
| **Two-tier countersigned ledger** | Core notarizes each node's chain head; a full-chain rewrite the edge chain accepts is **caught** by the notary (`tests/security/test_countersign.py`, 6/6) | BUILT · TESTED |
| **Edge hash chain** | Append-only SHA-256; edit/delete detected (`test_core.py` ledger cases); 6/6 border-scenario chain PASS | BUILT · TESTED |
| **Legal admissibility** | BSA §63(4)/§65B(4) certificate generated **from the ledger** | BUILT |
| **Supply-chain integrity** | Signed model manifest — SHA-256 verify per weight before load + HMAC-signed manifest (`edge/manifest.py`) | BUILT · TESTED |
| **AuthN/Z** | scrypt (n=2¹⁴) + per-user salt, constant-time compare; HMAC-signed expiring tokens; RBAC per endpoint | BUILT · TESTED |
| **Brute-force protection** | Per-(user,IP) sliding-window lockout, 429 + Retry-After (`LoginThrottle`) | BUILT · TESTED |
| **Sensor tamper detection** | Per-camera learnt-baseline: spray/blackout, glare, defocus, repoint, frozen-feed | BUILT |
| **Offline integrity** | Idempotent store-and-forward sync keyed on edge event id; outage recovery intact | BUILT · TESTED |
| **STRIDE threat model** | Per-component, every control Built/Partial/Planned | DOCUMENTED |
| **Full test suite** | `pytest` **184 passed / 3 skipped / 0 failed** | GREEN |

## AI application (how the evidence is generated)

| Capability | Figure | Kind |
|---|---|---|
| **Re-ID (Market-1501)** | OSNet **Rank-1 0.947 / mAP 0.845** (production default), vs ResNet-18 0.705/0.485 | MEASURED |
| **Face — YuNet (default)** | **AP@0.5 0.626** (WIDER FACE val), vs Haar 0.121; Apache/MIT, deployable | MEASURED |
| **Detection (MOT17 held-out)** | yolov8m@1280 **MOTA 0.435 / IDF1 0.563**, beats YOLOX-S 0.397/0.508; **108 fps** GPU (RTX 4050) | MEASURED |
| **ANPR (real Indian plates)** | Awiros specialist **4/4 exact** with plate-format filter, vs EasyOCR 2/4, fast-alpr 0/4; Apache-2.0 | MEASURED |
| **Scene anomaly (official protocol)** | UCSD Ped2 **frame-AUC 0.907 / RBDC 0.648 / TBDC 0.612** (Street Scene metrics) | MEASURED |
| **Normalcy (learned)** | day = routine (0.54) / night = unusual (21.4×) — learned via `observe()`, PASS | MEASURED |
| **Normalcy (false-alert load)** | routine open-border traffic suppressed while anomalies still alert | SIMULATED |

## Caveats (keep visible)

```
SIMULATED ≠ FIELD VALIDATED — no real SSB/CIBMS feed yet (the ask)
Ledger trust root is SYMMETRIC (HMAC) — HSM/asymmetric core signing = roadmap
No external anchoring of checkpoint heads yet — roadmap
mTLS edge↔core = PARTIAL (bearer token today); TLS by default = PLANNED
ONVIF onboarding = client + contract_check PASS; live-camera interop unproven
WIDER FACE test GT withheld — val-split figures only
Scene-anomaly on UCSD Ped2 (same official metrics), not MERL's 35 clips
```

**Release classification: SIH DEMO READY / EVIDENCE FROZEN — tamper-resistant ledger
built and tested; not yet production-field validated on a live border feed.**
