# Prahari — Final SIH 2026 Release-Readiness Audit

**Read-only audit. No features added, no models retrained/replaced/promoted, no
datasets downloaded, no pipeline behaviour changed, no GPU claims made, no
documented limitations removed.** Date: 2026-09-19.

> **Classification: SIH DEMO READY / EVIDENCE FROZEN.**
> Prahari is **not** production-field validated. Simulated results are system
> demonstrations, not field performance; real-footage results are generic scenes,
> not border footage.

---

## 1. Capability matrix (fixed status vocabulary)

Statuses: `IMPLEMENTED` · `TESTED` · `MEASURED` · `SIMULATED` · `CANDIDATE` ·
`UNVALIDATED` · `NOT DOWNLOADED` · `EVAL PENDING`.

| Capability | Status | Evidence |
|-----------|--------|----------|
| Person detection + tracking (MOT17) | **MEASURED** | 12.6 fps, MOTA 0.403, IDF1 0.500 — [validation-matrix.md](validation-matrix.md) |
| Detector fine-tune (YOLOv8n / MOT17) | **IMPLEMENTED** | `models/yolov8n_ft.onnx` (kept; not re-trained this cycle) |
| Cross-camera Re-ID (ResNet-18 / Market-1501) | **MEASURED** | Rank-1 0.705, mAP 0.485 (`reid.onnx`, production) |
| Re-ID retrain (`reid_retrain.onnx`) | **MEASURED · unpromoted** | Rank-1 0.715, mAP 0.4925 — kept beside, not promoted |
| Face detection — Haar (default) | **MEASURED** | WIDER FACE val, VOC AP@0.5 = 0.121 |
| Face detection — SCRFD-500M | **CANDIDATE (MEASURED)** | WIDER FACE val AP 0.489; not promoted — [model-provenance.md](model-provenance.md) |
| Real-footage detection test | **MEASURED (REAL-FOOTAGE)** | ground-level street 3,089 dets (2,898 person); aerial 3,691; subway failed — `var/real_clip_test.json` |
| Border scenario E2E (8 cases) | **SIMULATED** | 7/7 detection-correct, 6/6 evidence hash-chain PASS — [border-scenario-evaluation.md](border-scenario-evaluation.md) |
| NormalcyModel pattern-of-life | **SIMULATED** | NORMAL false-alert load 34→0; anomalies retained — [normalcy-validation.md](normalcy-validation.md) |
| Patrol suppression / deviation | **SIMULATED / TESTED** | matched→suppressed, deviation→escalated; fleet 71–97% — [patrol-suppression.md](patrol-suppression.md) |
| Evidence integrity (SHA-256 hash chain) | **TESTED** | `EvidenceLedger.verify()` PASS across alerting scenarios; `scripts/verify_evidence.py` |
| Offline / store-and-forward recovery | **TESTED** | network-outage recovery PASS — [recovery-validation.md](recovery-validation.md) |
| Security (credential encryption, auth) | **TESTED** | Fernet-encrypted camera creds — [security-validation.md](security-validation.md) |
| Camera capability profiling | **SIMULATED** | mounting-height error 0.2–2.2% live — [camera-profiling.md](camera-profiling.md) |
| Segmentation (GrabCut / SAM 2) | **MEASURED** | 87.5 ms / 421.2 ms — [validation-matrix.md](validation-matrix.md) |
| ANPR (LPRNet / EasyOCR) | **IMPLEMENTED** | capability-gated; field validation pending |
| Vehicle classification | **EVAL PENDING** | MIO-TCD identified, not run |
| Vehicle detection (BDD100K) | **EVAL PENDING** | dataset not downloaded |
| Scene normalcy (Street Scene) | **NOT DOWNLOADED** | 48.98 GB > disk; provenance recorded — [data-strategy.md](data-strategy.md) |
| Activity / event (VIRAT / MEVA) | **EVAL PENDING** | datasets not downloaded |
| ONVIF PTZ/Media negotiation | **UNVALIDATED** | node abstracts via RTSP — [release-readiness.md](release-readiness.md), [operational-gap-audit.md](operational-gap-audit.md) |
| Visible↔Thermal Re-ID | **UNVALIDATED** | no cross-modal dataset — [validation-matrix.md](validation-matrix.md) |

## 2. Benchmark artifact reconciliation

| Artifact | Result | Kind | Source |
|----------|--------|------|--------|
| Detector / MOT17 | 12.6 fps · MOTA 0.403 · IDF1 0.500 | MEASURED | validation-matrix.md |
| Re-ID / Market-1501 | Rank-1 0.705 · mAP 0.485 (prod); 0.715 · 0.4925 (retrain, unpromoted) | MEASURED | benchmark-matrix.md, `var/reid_market_evaluation.json` |
| WIDER FACE / Haar | AP@0.5 0.121 (val) | MEASURED | `var/widerface_val_haar.json` |
| Haar vs SCRFD | AP 0.121 → 0.489; latency 74.6 → 18.8 ms **CPU** (no GPU claim) | MEASURED / CANDIDATE | `var/widerface_val_scrfd_cpu.json` |
| Real-footage test | street 3,089 dets (2,898 person); aerial 3,691; subway 36 (failed) | MEASURED (REAL-FOOTAGE) | `var/real_clip_test.json` |
| Border-scenario E2E | 7/7 detection-correct · 6/6 evidence PASS | SIMULATED | `var/border_scenario_evaluation.json` |
| NormalcyModel | NORMAL 34→0 false-alert load; night/off-route/patrol retained | SIMULATED | `var/normalcy_validation.json` |
| Patrol suppression | matched suppressed · deviation escalated; fleet 71–97% | SIMULATED / TESTED | patrol-suppression.md |
| Evidence integrity | hash chain verify PASS | TESTED | verify_evidence.py |
| Backup / recovery | outage recovery PASS | TESTED | recovery-validation.md; `scripts/backup_db.py` |
| Security validation | Fernet creds, auth, tamper | TESTED | security-validation.md |

## 3. Provenance & licensing — present

- [model-provenance.md](model-provenance.md) — YOLOX, ByteTrack, ResNet Re-ID, SAM2, ANPR, **SCRFD-500M (candidate, non-commercial/research-only weights, promotion gate)**.
- [DATA_LICENSES.md](../DATA_LICENSES.md) — all datasets (MOT17, BDD100K, VIRAT, MEVA, VisDrone, Street Scene, UCF-Crime, WIDER FACE, CCPD, UFPR-ALPR, MIO-TCD, Market-1501) + real-footage test clips (Pexels) with SHA-256 where downloaded.
- [data-strategy.md](data-strategy.md) — acquisition log (WIDER FACE downloaded+verified; Street Scene NOT DOWNLOADED).

## 4. Production / default model configuration — verified unchanged

| Item | Expected | Verified |
|------|----------|----------|
| Default face detector | Haar | ✅ `build_face_detector(backend="haar")` default (`prahari/edge/detect/face.py`) |
| SCRFD | candidate, not promoted | ✅ opt-in only via `build_face_detector("scrfd")`; docs mark CANDIDATE |
| Production detector | unchanged | ✅ YOLOX/YOLOv8 ONNX untouched (original timestamps) |
| `reid.onnx` | production | ✅ present, Sep 16 timestamp, unchanged |
| `reid_retrain.onnx` | unpromoted | ✅ present beside, not wired as production |

## 5. Known limitations — explicitly documented

| Limitation | Documented in |
|-----------|---------------|
| ONVIF WSDL / interoperability **UNVALIDATED** | release-readiness.md, operational-gap-audit.md |
| SCRFD pretrained-weight licensing restriction | model-provenance.md |
| No SCRFD GPU benchmark (CUDA EP unavailable) | benchmark-matrix.md, normalcy/face notes |
| WIDER FACE test GT unavailable (val only) | benchmark-matrix.md, data-strategy.md |
| Street Scene **NOT DOWNLOADED** (49 GB) | data-strategy.md |
| Simulated border results ≠ field performance | border-scenario-evaluation.md |
| Seeded NormalcyModel baseline ≠ long-term field baseline | normalcy-validation.md |

## 6. Repository cleanliness — verified

- **No large datasets/binaries committed** — `git ls-files` shows no `.mp4/.onnx/.zip/.pt/.pth/.avi`.
- **Demo & real-footage MP4s gitignored** — confirmed via `git check-ignore`.
- **No tracked secrets** — no `.env/.pem/.key/credential` files tracked; camera creds are Fernet-encrypted at rest.
- **Generated artifacts intentional** — `var/*.json` results are gitignored (reproducible); tracked additions are documentation, scripts, and ground-truth JSONs only.
- **Working tree** contains the milestone evidence (docs, scripts, DATA_LICENSES, presentation, border-scenario ground-truth JSONs) as intentional tracked additions; `var/` and `*.log` remain ignored.
- **Nit (pre-existing, not modified):** `tree_output.txt` (~11 MB text) is tracked — predates this cycle; flagged, not changed (read-only audit).

## 7. Release tag

- **`v1.0.0-sih2026` exists** (and `v1.0.0-sih2026-rc1`). **No new tag created** — per instruction, only if something changed.
- The evidence artifacts from this audit cycle are present in the **working tree (uncommitted)** and therefore post-date the tag. Committing/tagging them is a user decision; this audit does not create a tag.

---

## 8. One-page SIH judge-facing evidence summary

**Problem.** Extract additional operational intelligence from *existing,
heterogeneous CCTV* on India's borders — including open (Indo-Nepal/Bhutan)
sectors with heavy lawful traffic — with edge processing, evidence-backed alerts,
and resilience across connectivity loss. Prahari does not replace CIBMS.

**Architecture.** Existing CCTV → RTSP/ONVIF → measured **camera-capability
profiling** → edge AI **detection + tracking** → **context/normalcy/patrol
rules** (two doctrines: fenced tripwire vs open pattern-of-life) → **alert
governor** (rationed alerting) → **evidence + SHA-256 hash chain** → core
dashboard → operator.

**Key measured results.**
- Person detection/tracking (MOT17): **12.6 fps, MOTA 0.403, IDF1 0.500**.
- Cross-camera Re-ID (Market-1501): **Rank-1 0.705, mAP 0.485**.
- Face detection (WIDER FACE val): Haar **AP 0.121** (default) vs SCRFD-500M
  **AP 0.489** (candidate, CPU; no GPU claim; research-only weights).
- Real-footage detection (ground-level street): **2,898 person detections**
  across a clip — the detector works on real pixels (generic scenes, not border).

**Demo scenarios (SIMULATED).** 8-case border set: **7/7 correct** event/decision,
**6/6 evidence hash-chain PASS**; patrol matched→suppressed vs deviation→escalated;
tamper detected; **network-outage integrity preserved**. NormalcyModel puts
pattern-of-life in the loop: **NORMAL open-border false-alert load 34 → 0** with
no anomaly regression.

**Security / evidence.** Fernet-encrypted camera credentials; append-only
SHA-256 evidence ledger verified independently; offline store-and-forward with
integrity preserved on reconnect.

**Limitations (stated, not hidden).** ONVIF negotiation UNVALIDATED; SCRFD weights
non-commercial/research-only and not promoted; no SCRFD GPU benchmark; WIDER FACE
test GT unavailable (val only); Street Scene not downloaded; simulated results are
demonstrations, not field performance; seeded normalcy baseline is not a long-term
field baseline.

**Deployment model.** Edge-first per camera, sector core aggregation, resilient to
uplink loss; works on installed CCTV without new sensors. **Not production-field
validated** — prototype/demonstration maturity with honest, reproducible evidence.

---

**RELEASE CLASSIFICATION: SIH DEMO READY / EVIDENCE FROZEN.**
Not production-field validated.
