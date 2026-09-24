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
| Detection throughput (GPU) | YOLOv8n @640 | synthetic frames | Throughput | 108 fps / 9.3 ms per frame | GPU (RTX 4050 Laptop, ultralytics/torch) | MEASURED |
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
| Face detection — YuNet (deployable) | WIDER FACE | val (VOC AP@0.5, all valid faces) | AP | 0.626 | CPU (ONNX, 0.23 MB) | MEASURED · DEPLOYABLE (Apache/MIT) |
| ANPR (detection) | Real Indian plates (13 imgs) | field images | Plate localised | 12/13 | CPU (fast-alpr) | MEASURED · REAL-FOOTAGE |
| ANPR (OCR) — fast-alpr global | Real Indian plates (4 labelled) | field images | Exact / char-sim | 0/4 · 0.76 | CPU | MEASURED |
| ANPR (OCR) — EasyOCR (crop+upscale) | Real Indian plates (4 labelled) | field images | Exact / char-sim | **2/4 · 0.89** | CPU | MEASURED · India-preferred |
| UAV detection | VisDrone | test | mAP | — | — | PENDING |
| Visible↔Thermal Re-ID | (no dataset) | — | — | — | — | PENDING |

## Notes on interpretation

- **MOT17 numbers are the honest ceiling of the current CPU prototype.** 12.6 fps
  means real-time multi-camera tracking needs a modern GPU/edge accelerator —
  this is stated, not hidden.
- **Detector-accuracy training experiment (honest negative result).** An attempt to
  lift the MOTA/IDF1 metric by training/swapping detectors did **not** beat the
  existing YOLOX-S baseline. Measured on held-out MOT17-02/04 via the reproducible
  `scripts/eval_mot_tracking.py` (full-sequence CLEAR-MOT, no profiling gate):
  YOLOX-S **MOTA 0.397 / IDF1 0.508** (best); yolov8m COCO (correct RGB) 0.377 / 0.511;
  yolov8s fine-tuned on MOT17 0.331 / 0.408; yolov8n_ft 0.311 / 0.386. **Fine-tuning
  on the small 2,916-image MOT17 subset hurt** relative to a COCO-pretrained model,
  and a bigger COCO model (yolov8m) still did not surpass YOLOX-S on MOTA. This is
  exactly the point that *meaningful detection-accuracy gains need better/more
  (ideally real) data, not a bigger model on the same academic subset*. YOLOX-S
  remains the production detector; nothing was promoted. **Genuine wins from the
  effort:** (1) a real bug fixed — `OnnxYoloDetector` was silently misdecoding *all*
  YOLOv8 ONNX models (a flood of ~5,000 saturated boxes from a wrong-scale
  preprocessing probe); the probe now rejects saturation floods, so YOLOv8 models
  decode correctly pipeline-wide; (2) `scripts/eval_mot_tracking.py`, a reproducible
  CLEAR-MOT/IDF1 harness for held-out sequences.
- **The GPU path is now measured: 108 fps (9.3 ms) on an RTX 4050 Laptop GPU**
  (YOLOv8n @640, ultralytics/torch), ~8.5× the CPU. This is the accelerated path a
  deployment ships on, and it makes ~3 cameras real-time (30 fps each) *on a laptop
  GPU* — an edge accelerator (Jetson Orin class) is expected higher, which is what
  the cameras-per-node cost assumption in [deployment-cost-model.md](deployment-cost-model.md)
  rests on. Honest caveats: this is the **ultralytics/torch** GPU path on **yolov8n**,
  not the ONNX-Runtime CUDA deployment runtime (ORT's CUDA EP needs cuDNN 9.x, absent
  here), and it is a laptop GPU, not the target edge hardware. It removes the "no GPU
  benchmark / not real-time" gap while staying precise about what was measured.
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
- **Face detection: three backends, same protocol — YuNet is the deployable winner.**
  All measured by `scripts/evaluate_widerface.py` on the identical official val
  split under the identical VOC-style AP@0.5 definition (Haar re-run reproduces
  0.1211 exactly, confirming apples-to-apples):

  | Detector | AP@0.5 | Precision | Recall | Size | License | Status |
  |---|---|---|---|---|---|---|
  | Haar (baseline) | 0.121 | 0.664 | 0.131 | 0.93 MB | OpenCV BSD | wired default |
  | SCRFD-500M | 0.489 | 0.749 | 0.506 | 2.30 MB | **research-only** | candidate (blocked) |
  | **YuNet 2023mar** | **0.626** | 0.553 | **0.662** | **0.23 MB** | **Apache/MIT** | **DEPLOYABLE** |

  **YuNet is now the recommended deployable face backend** — highest AP (5× Haar,
  and above SCRFD), smallest model (0.23 MB), and a **clean Apache/MIT license with
  no deployment blocker** (`build_face_detector("yunet")`, `prahari/edge/detect/face.py`).
  This resolves the earlier licensing gate: SCRFD stays a research-only *candidate*,
  but Prahari no longer depends on non-commercial weights for a strong face detector.
  Haar remains the wired *default* pending an explicit promotion decision; YuNet is
  the intended upgrade. Caveats unchanged: (1) VOC-style AP@0.5 over all valid faces,
  not the official easy/medium/hard protocol; (2) **val split only** (test GT withheld);
  (3) figures are **CPU** — no GPU face benchmark is claimed.

## How to fill a PENDING row

1. Acquire the dataset (record provenance in [`../DATA_LICENSES.md`](../DATA_LICENSES.md)).
2. Run the matching harness (`scripts/evaluate*.py` / `scripts/benchmark_*.py`).
3. Copy the number and the exact hardware string from the run output.
4. Change the row's status to MEASURED and cite the run log.

Never edit a result by hand to a value a run did not produce.
