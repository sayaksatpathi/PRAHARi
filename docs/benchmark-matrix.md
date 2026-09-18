# Prahari Benchmark Matrix

One table, one rule: **a number appears here only after it comes from an actual
run.** Anything not yet measured is `PENDING`, never a placeholder that could be
mistaken for a result. This keeps presentation claims from quietly becoming
stronger than the experiments behind them.

Status legend:
- **MEASURED** — produced by a real evaluation run; source cited.
- **SIMULATED** — produced against the deterministic simulated fleet, not a trained network on real footage.
- **DATA ACQUIRED / EVAL PENDING** — dataset downloaded and verified on disk, but no evaluation run yet. No number.
- **PENDING** — dataset planned but not yet on disk, run not yet performed. No number.

Cross-references:
[`validation-matrix.md`](validation-matrix.md) ·
[`evaluation.md`](evaluation.md) ·
[`data-strategy.md`](data-strategy.md) ·
[`../DATA_LICENSES.md`](../DATA_LICENSES.md)

## Capability × Dataset × Metric

| Capability | Dataset | Split | Metric | Result | Hardware | Status |
|---|---|---|---|---|---|---|
| Person detection | MOT17 | held-out 02/04 | Throughput | 12.6 fps / 79.4 ms per frame | CPU (see bench logs) | MEASURED |
| Tracking (ByteTrack logic) | MOT17 | held-out 02/04 | MOTA | 0.403 | CPU | MEASURED |
| Tracking (ByteTrack logic) | MOT17 | held-out 02/04 | IDF1 | 0.500 | CPU | MEASURED |
| Cross-camera Re-ID (ResNet-18) | Market-1501 | test | Rank-1 / mAP | 0.705 / 0.485 | CPU | MEASURED |
| Cross-camera Re-ID (HSV fallback) | Market-1501 | test | Rank-1 / mAP | 0.097 / 0.030 | CPU | MEASURED |
| Segmentation (GrabCut) | MOT17-04 | test | Latency | 87.5 ms (11.4 fps) | CPU | MEASURED |
| Segmentation (SAM 2) | MOT17-04 | test / synthetic | Latency / IoU | 421.2 ms (2.4 fps) / IoU 0.975 (synthetic) | GPU | MEASURED / SIMULATED |
| Camera self-profiling | Simulated fleet | 5 cameras | Mounting-height error | 0.2–2.2% (live), exact on clean GT | CPU | SIMULATED |
| Alert suppression | Simulated fleet | 5 cameras | Rule events suppressed | 71–97% | CPU | SIMULATED |
| Border scenario E2E | Border Scenario Set (8) | simulated clips | Detection-correct / evidence-chain | 7/7 · 6/6 PASS | CPU | SIMULATED / DEMO |
| Normalcy suppression | NormalcyModel (seeded) | NORMAL_OPEN_BORDER | False-alert load with vs without | 34 → 0 (56 suppressed) | CPU | SIMULATED |
| Normalcy — anomaly retention | NormalcyModel (seeded) | NIGHT / OFF_ROUTE / patrol | Alerts retained (no regression) | night 36 · off-route 28 · patrol supp/esc | CPU | SIMULATED |
| Vehicle detection | BDD100K | test | mAP | — | — | PENDING |
| Scene normalcy | Street Scene | test | Anomaly AUC | — | — | PENDING |
| Activity / event | VIRAT / MEVA | test | Event metric | — | — | PENDING |
| Face detection — Haar (baseline) | WIDER FACE | val (VOC AP@0.5, all valid faces) | AP | 0.121 | CPU (Haar cascade, 0.93 MB) | MEASURED · BASELINE |
| Face detection — SCRFD-500M (candidate) | WIDER FACE | val (VOC AP@0.5, all valid faces) | AP | 0.489 | CPU (ONNX, 2.30 MB) | MEASURED · CANDIDATE |
| ANPR | CCPD / UFPR-ALPR | test | Plate accuracy | — | — | PENDING |
| UAV detection | VisDrone | test | mAP | — | — | PENDING |
| Visible↔Thermal Re-ID | (no dataset) | — | — | — | — | PENDING |

## Notes on interpretation

- **MOT17 numbers are the honest ceiling of the current CPU prototype.** 12.6 fps
  means real-time multi-camera tracking needs a modern GPU/edge accelerator —
  this is stated, not hidden.
- **Re-ID Rank-1 0.705 is on Market-1501**, an academic dataset. Domain shift to
  real border CCTV is unmeasured; the border-scenario set (below) is how we will
  eventually close that gap.
- **SIMULATED rows characterise the modelled behaviour of the synthetic detector**,
  not a trained network on real footage. They are legitimate for showing pipeline
  behaviour (suppression, profiling, latency) but are not accuracy claims.
- **Border scenario E2E is SIMULATED / DEMO** — 8 controlled clips through the
  unchanged production pipeline (detection → tracking → rules → governor →
  evidence + hash chain, plus patrol/tamper/outage). 7/7 fired the correct event
  type or friendly-force/integrity decision; evidence hash chain verified on all
  6 alerting scenarios; outage integrity preserved. The false-alarm *rate* is the
  documented pre-normalcy upper bound (short clips under-exercise the normalcy +
  governor rationing). Full write-up: [border-scenario-evaluation.md](border-scenario-evaluation.md).
  **Never present as real-world border performance.**
- **Normalcy rows measure the learned pattern-of-life layer IN the decision path**
  (not the pre-normalcy upper bound). With a seeded day-busy baseline,
  `NORMAL_OPEN_BORDER` lawful traffic is classified routine and its false-alert
  load drops **34 → 0** (56 soft events suppressed), while NIGHT/OFF_ROUTE/patrol
  all still alert (no true-positive regression); normalcy decision latency ~0.1 ms.
  Caveat: the baseline is **seeded (a demo affordance), not learned from weeks of
  field traffic**, and clips are short — so these are classification/suppression
  correctness results, not long-term field rates. Full write-up and limitations:
  [normalcy-validation.md](normalcy-validation.md).
- **PENDING rows have datasets identified but no run.** Do not present a PENDING
  capability as validated.
- **Face detection: SCRFD-500M (CANDIDATE) vs Haar (BASELINE), same protocol.**
  Both measured by `scripts/evaluate_widerface.py` on the identical official val
  split under the identical VOC-style AP@0.5 definition (Haar re-run under the same
  code reproduces 0.1211 exactly, confirming apples-to-apples):

  | Detector | AP@0.5 | Precision | Recall | Latency/img | FPS | Provider | Size |
  |---|---|---|---|---|---|---|---|
  | Haar (baseline) | 0.121 | 0.664 | 0.131 | 74.6 ms | 13.4 | CPU | 0.93 MB |
  | SCRFD-500M (candidate) | **0.489** | **0.749** | **0.506** | **18.8 ms** | **53.2** | CPU | 2.30 MB |

  SCRFD is **~4× the AP, ~3.9× the recall, higher precision, and ~4× faster on CPU**
  for +1.4 MB. It is classified **CANDIDATE** and **not promoted** — the Haar
  cascade remains the wired-in default (`prahari/edge/detect/face.py`); SCRFD is a
  separate ONNX backend (`prahari/edge/detect/scrfd.py`) reached via
  `build_face_detector("scrfd")`. Caveats: (1) VOC-style AP@0.5 over all valid
  faces, *not* the official easy/medium/hard MATLAB protocol; (2) **val split only**
  (test GT withheld — no test claim); (3) **GPU not measured** — ONNX Runtime's CUDA
  provider failed to load in this environment (missing cuDNN 9.x) and fell back to
  CPU, so both figures are CPU; GPU is expected faster but is not claimed here;
  (4) SCRFD weights are **InsightFace non-commercial/research-only** (see
  [model-provenance.md](model-provenance.md)). Promotion is a separate, explicit step.

## How to fill a PENDING row

1. Acquire the dataset (record provenance in [`../DATA_LICENSES.md`](../DATA_LICENSES.md)).
2. Run the matching harness (`scripts/evaluate*.py` / `scripts/benchmark_*.py`).
3. Copy the number and the exact hardware string from the run output.
4. Change the row's status to MEASURED and cite the run log.

Never edit a result by hand to a value a run did not produce.
