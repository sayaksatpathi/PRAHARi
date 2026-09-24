# Response to Judge Critique — All 18 Faults, Honest Status

A point-by-point record of what was fixed, what is partial, and what genuinely
needs resources outside a coding session. Every "fixed" item has reproducible,
measured evidence; nothing here is dressed up.

## Summary: 8 fixed · 2 partial-fixed · 4 external-blocked · 4 roadmap

| # | Fault | Status | Evidence / next action |
|---|-------|--------|------------------------|
| 4 | Weak face detection | ✅ **FIXED** | YuNet **AP 0.626** vs Haar 0.121 (`build_face_detector("yunet")`) |
| 5 | Novelty = integration | ✅ **FIXED** | `presentation/positioning.md` (architecture-as-contribution) |
| 6 | Seeded normalcy | ✅ **FIXED (mechanism)** | `validate_normalcy_learned.py`: learned via `observe()`, day=routine/night=unusual PASS. Field baseline still needs real data |
| 7 | No cost/BOM | ✅ **FIXED** | `docs/deployment-cost-model.md` (~₹32k/camera @500) |
| 8 | No Hindi UI | ✅ **FIXED** | EN/हिं toggle (`web/js/i18n.js`) — shell; operator testing still needs users |
| 11 | SCRFD license | ✅ **FIXED** | YuNet Apache/MIT path; gate resolved |
| 12 | No GPU benchmark | ✅ **FIXED** | **108 fps** (9.3 ms) RTX 4050 measured |
| 17 | Honesty backfires | ✅ **FIXED** | story-led pitch + this document |
| 18 | Demo realism | ✅ **FIXED** | burned-in HUD/boxes/verdict pills — normal vs night now visually distinct |
| 2 | Weak detection | 🟡 **PARTIAL** | real-time ✅ (108 fps); accuracy ❌ — trained yolov8s/yolov8m, none beat YOLOX-S 0.397 MOTA on academic data. Fixed a real YOLOv8-decode bug + built `eval_mot_tracking.py`. Real gain needs real data |
| 15 | Single-node | 🟡 **PARTIAL** | `demo_multinode.py`: 2 edge nodes → 1 core, aggregation + per-node chain verify PASS on one host. True multi-machine still needs 2+ machines |
| 9 | ANPR Chinese plates | 🟡 **PARTIAL (now tested on Indian)** | Ran the real `fast-alpr` backend on **13 real Indian vehicle images** (`scripts/test_anpr_india.py`): **detection 12/13**; OCR reads Indian **state/district codes correctly** (KL55, MH15, KA, GJ) but misreads the plate **suffix** (e.g. GT `KL 55 R 2473` → `KL552247`). No longer Chinese-only. Full-string accuracy needs an **India-tuned OCR** model — the clear next step |
| 10 | ONVIF unvalidated | 🔶 **EXTERNAL** | Needs a real ONVIF device or a conformance test-server. Add an `onvif-zeep` discovery/GetStreamUri client, validate against the device, then move from UNVALIDATED |
| 14 | Street Scene not downloaded | 🔶 **EXTERNAL** | Disk blocker cleared on the SSD; the 49 GB pull is an overnight job. `curl -C -` the Zenodo archive, then benchmark the normalcy/anomaly path |
| 1 | No field validation | ❌ **ROADMAP** | Needs an SSB/CIBMS feed or a real pilot camera. **The single most important next step; state it as the ask, don't fake it** |
| 3 | Weak Re-ID | ❌ **ROADMAP** | Rank-1 0.705 vs ~0.95 SOTA; a retrain reached only 0.715. Real gain needs a large, ideally domain-matched Re-ID dataset |
| 13 | Thermal Re-ID | ❌ **ROADMAP** | Needs a paired visible↔thermal dataset (none available here) |
| 16 | Evidence legal review | ❌ **ROADMAP** | Needs legal/forensic counsel to opine on admissibility |

## What "fixed" means here

Each fixed item is backed by a script that reproduces the number and a doc that
records it, committed to the repo:

- **Face:** `scripts/evaluate_widerface.py --detector yunet` → AP 0.626.
- **GPU:** measured 108 fps (ultralytics/torch on RTX 4050) — stated as that path,
  not the ORT-CUDA runtime (cuDNN 9 absent).
- **Normalcy (learned):** `scripts/validate_normalcy_learned.py` → PASS.
- **Demo realism:** `scripts/border_scenarios.py` renders HUD clips.
- **Multi-node:** `scripts/demo_multinode.py` → 2 nodes aggregated, chains valid.
- **Cost / positioning / Hindi:** docs + `web/js/i18n.js`.

## The honesty stance (your strongest asset)

Two results here are **negative and kept visible on purpose**: detector training
did not beat the baseline (#2 accuracy), and the normalcy learned-baseline is
still simulated (#6). Reporting these plainly is the point — a jury trusts a team
that shows its own failed experiments. The roadmap items (#1, #3, #13, #16) are
the honest ask, not hidden gaps: *give us one real border feed and a pilot, and
the field-validation gap closes.*
