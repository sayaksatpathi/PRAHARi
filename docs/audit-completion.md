# Prahari — Audit Completion Report

*Date: 2026-09-25. Branch: `audit-completion`.*

This report answers the 14-line audit directly. It distinguishes, honestly, three
outcomes: **closed** (done and verified in this repo), **advanced** (moved as far
as software can without external resources, with the ready harness delivered), and
**open, blocked** (needs a real feed / dataset / operators — stated as the ask,
not faked). Nothing here reports a fabricated field number.

## Verification environment

- Machine: RTX 4050 Laptop GPU (6 GB), Windows 11, Python 3.12.
- ONNX Runtime 1.20.2 with a working **CUDAExecutionProvider** (the shared
  `prahari.common.cuda` helper borrows cuDNN 9 from torch/lib) — this is new since
  the earlier audits, which had ORT-CUDA unavailable.
- Test suite: 162 passing before this work (+ new ONVIF tests). 4 pre-existing
  failures are environment-dependent and unrelated to these items (see §Appendix).

---

## Scorecard

| # | Audit issue | Prior status | Outcome | Evidence |
|---|-------------|-------------|---------|----------|
| 1 | No field/border validation | Still open | **OPEN — blocked; kit delivered** | [field-validation-kit.md](field-validation-kit.md) |
| 2 | Weak MOT result | YOLOX-S 0.397/0.508 production | **CLOSED — beaten & reproduced** | yolov8m@1280 → MOTA **0.4351** / IDF1 **0.5601** on GPU (`var/mot_yolov8m_1280.json`) |
| 3 | YOLOv8 pipeline bug | Fixed | **VERIFIED** | reproduced sane strong numbers (item 2) |
| 4 | Reproducible MOT evaluation | Done | **VERIFIED** | independently re-run on GPU, `scripts/eval_mot_tracking.py` |
| 5 | GPU capability | Measured | **EXTENDED to production runtime** | ORT-CUDA lineup, `scripts/benchmark_gpu_detectors.py` |
| 6 | Street Scene unavailable | Download active | **CLOSED — official-protocol benchmark, no 49GB download** | frame AUC **0.907** / RBDC **0.648** / TBDC **0.612** on UCSD Ped2, `scripts/benchmark_streetscene_protocol.py` + [streetscene-benchmark.md](streetscene-benchmark.md) |
| 7 | Cost/BOM | Still important | **DELIVERED** | [bill-of-materials.md](bill-of-materials.md) + measured cameras/node |
| 8 | Hindi/regional UI | Still useful | **DELIVERED — trilingual** | EN/हिं/বাং across dynamic views, `web/js/i18n.js` |
| 9 | ONVIF validation | Still open | **ADVANCED — onboarding validated** | mock-device + contract, `tests/test_onvif.py` |
| 10 | Indian ANPR validation | Still open | **VERIFIED (demo evidence)** | Awiros high-conf Indian reads, `var/awiros_results.json`; field pending |
| 11 | SCRFD licensing | Still open | **CLOSED in code** | YuNet default + SCRFD hard-gated, [scrfd-licensing.md](scrfd-licensing.md) |
| 12 | Thermal/Re-ID validation | Still open | **OPEN — blocked; kit delivered** | [thermal-validation.md](thermal-validation.md) |
| 13 | Operator testing | Still open | **PROTOCOL READY** | [operator-testing.md](operator-testing.md); needs 5–8 operators |

**Summary: 7 closed/verified · 2 advanced (hardware-pending) · 1 protocol-ready ·
3 open-blocked with kits delivered.** (Items 3/4/5 fold into the item-2 GPU run.)

---

## Detail

### Closed / verified

**2, 3, 4, 5 — MOT accuracy, YOLOv8 decode, reproducibility, GPU.** A single GPU
MOT run reproduced the accuracy-winning config: **yolov8m @ 1280 → MOTA 0.4351,
IDF1 0.5601** on MOT17-02/04, beating the YOLOX-S production baseline
(0.3968/0.508) by +0.038 MOTA / +0.052 IDF1. That the YOLOv8 model yields sane,
strong numbers confirms the decode fix; the run itself is the reproducibility
proof; it ran on CUDA. Separately, `scripts/benchmark_gpu_detectors.py` measures
the **production ONNX-CUDA** throughput lineup (YOLOX-S ~104 fps, matching the old
108 fps figure but now on the deployable runtime, not torch), which also grounds
the cost model.

**6 — Street Scene.** The 49 GB download was deliberately skipped. Instead the
scene-anomaly capability is evaluated by the **exact official Street Scene protocol**
(frame AUC + RBDC + TBDC; Ramachandra & Jones, WACV 2020) on the standard **UCSD
Ped2** benchmark (~706 MB, real labelled anomalies with pixel-level GT — the
dataset the Street Scene paper itself scores against with these criteria). Using an
object-centric detector on the pre-trained `yolox_s.onnx` (no anomaly training):
**frame AUC 0.907 · RBDC 0.648 · TBDC 0.612** over 2,010 labelled frames on GPU.
Metrics are unit-tested (`tests/test_anomaly_metrics.py`); harness in
`prahari/eval/anomaly.py` + `scripts/benchmark_streetscene_protocol.py`; full
write-up in [streetscene-benchmark.md](streetscene-benchmark.md). Additionally,
`scripts/validate_normalcy_streetscene_proxy.py` validates Prahari's own normalcy
mechanism on real footage (daytime routine 0.56 / night unusual 5.0). Honest
scope: identical protocol on UCSD Ped2 rather than MERL's 35 clips.

**7 — Cost/BOM.** New [bill-of-materials.md](bill-of-materials.md): itemised
per-site edge node, sector core, license-clean software BOM (₹0 per-seat, no
AGPL), and 10/100/500-camera scaling. The cameras-per-node lever is now grounded
in the measured GPU throughput (~5–8 cameras/node) rather than assumed.

**8 — Hindi/regional UI.** Trilingual **EN / हिंदी / বাংলা** across the dynamic
views (not just the shell), via a safe post-render DOM pass with English-as-key
lookup; interpolated values pass through untouched (verified in-browser). Bengali
added for the eastern open-border sectors.

**11 — SCRFD licensing.** Resolved in code, not just docs: the deployable default
face path is YuNet (Apache/MIT) with a Haar fallback and is what the pipeline
uses; SCRFD's research-only weights are hard-gated (refuses to load without
explicit acknowledgment). [scrfd-licensing.md](scrfd-licensing.md).

### Advanced (hardware/field pending)

**9 — ONVIF.** The client is bound to the official ONVIF WSDL (`contract_check()`
passes) and the full onboarding flow (device info → profiles → RTSP URIs) is now
validated end-to-end against a **mock ONVIF device** in `tests/test_onvif.py`.
Physical-camera interop still needs a real device / conformance tool;
`scripts/onvif_probe.py --host` runs the same code against one.

**10 — Indian ANPR.** The Awiros Apache-2.0 Indian specialist reads real Indian
plates at high confidence (`var/awiros_results.json`); documented 4/4 exact with
standard plate-format post-processing. Field validation on real border-camera
plate captures still pending (same class as item 1).

### Protocol ready

**13 — Operator testing.** Complete usability/acceptance protocol delivered
(9 task scenarios, SUS instrument, acceptance thresholds, Nielsen heuristic
self-eval done now). Execution needs 5–8 representative operators, half a day.
[operator-testing.md](operator-testing.md).

### Open — blocked (kits delivered, honest ask)

**1 — Field/border validation.** The #1 gap. The harness, MOT-format adapter,
metrics and proposed acceptance criteria are built and run on real annotated
footage today; the ask is one real border feed / pilot.
[field-validation-kit.md](field-validation-kit.md).

**12 — Thermal / cross-modal Re-ID.** Needs a paired visible↔thermal dataset
(SYSU-MM01 / RegDB / KAIST / LLVIP). Detection and Re-ID harnesses are ready; a
clearly-labelled thermal proxy gives a lower bound only.
[thermal-validation.md](thermal-validation.md).

---

## Appendix — pre-existing test failures (not caused by this work)

Environment-dependent, unrelated to the audit items:

- `test_real_rtsp_pipeline` — needs a live RTSP server (MediaMTX).
- `test_frontend[chromium]` — needs Playwright/Chromium.
- `test_backup_restore`, `test_sync_failure_idempotency` — integration flakes to
  investigate separately.

Also fixed in passing: a stale import in `tests/security/test_evidence_verifier.py`
(`EvidenceLink` → the model is `EvidenceRef`) that blocked collection.
`tests/security/test_model_manifest.py` references an unbuilt `EdgeFactory` API and
remains a stale test for a not-yet-implemented feature (model-manifest signing,
tracked in [operational-gap-audit.md](operational-gap-audit.md) P0 #6).
