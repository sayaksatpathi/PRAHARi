# 🛡️ Prahari — Edge AI Video-Intelligence for CCTV

**AI video-intelligence layer for existing CCTV infrastructure.**
SIH26187 · Ministry of Home Affairs / Sashastra Seema Bal · Smart India Hackathon 2026

> Prahari turns already-installed CCTV into camera-aware, edge-processed, evidence-backed border intelligence that keeps working when the uplink does not.

---

## What it is (and isn't)

Prahari does **not** replace CIBMS or claim border surveillance lacks AI. Object detection, tracking, and ANPR are solved problems — **Prahari's contribution is the deployment architecture**: extracting operational intelligence from existing, heterogeneous CCTV through measured capability profiling, edge-first processing, evidence-backed events, and resilient operation across connectivity loss.

## What's actually different

1. **Capability profiling is measured, not declared.** Recovers effective resolution, delivered frame rate, noise floor, and compression damage; auto-recovers the ground plane from pedestrian bounding boxes (no survey/calibration target). Issues a Camera Capability Certificate against IEC 62676-4 DORI bands. *Self-calibration lands within **0.4–3.2%** of known mounting height across a 5-camera fleet.*
2. **Two doctrines.** Tripwire/restricted-zone rules for fenced sectors; pattern-of-life rules for open borders (Indo-Nepal/Bhutan) where a tripwire would fire thousands of times a day.
3. **Alerting is rationed; recording is not.** A governor budgets operator attention — raising the score threshold when events exceed the hourly budget. *Measured: a 90-second window went from **1064 → 138 events**, with 81% recorded without interrupting anyone.*
4. **ANPR that refuses to guess.** Plate reading runs only inside image regions certified to ≥250 px/m.
5. **Evidence sealed in a hash chain.** Each event commits to the hash of the prior one — tamper-evident, checkable after offline backlogs.
6. **Offline is the design point.** Detection never depends on the uplink; metadata is pushed, video is pulled; records are never dropped under storage pressure.
7. **Cross-camera corridor.** A camera topology graph with learnt transition times links tracks across cameras into global identities.

## Tech Stack

| Layer | Tools |
|------|-------|
| CV | Object detection, tracking, ANPR (OpenCV) |
| Services | Python, FastAPI (edge node + sector core) |
| Data | Camera capability profiling, hash-chained event store, SQLite |

## Quick Start

```bash
python -m venv .venv --system-site-packages
.venv/bin/python -m pip install -r requirements.txt

# Edge node (runs standalone):
.venv/bin/python -m uvicorn prahari.edge.app:app --port 8420   # http://127.0.0.1:8420

# Optional sector core:
.venv/bin/python -m uvicorn prahari.core.app:app --port 9420
```

## Verify

```bash
python -m pytest tests/ -q
python scripts/smoke_profiling.py     # ground-plane self-calibration vs known truth
python scripts/evaluate.py            # deterministic replay harness → var/eval/evaluation.html
```

## Limitations

Tamper-**evident**, not tamper-proof. Metrics from a simulated 5-camera fleet plus a live pipeline; `docs/` is explicit about which numbers are ground-truth-validated vs. plumbing checks.

---

*Team project · Smart India Hackathon 2026.*
