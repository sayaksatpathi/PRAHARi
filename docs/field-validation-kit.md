# Prahari — Field / Border Validation Kit

**Audit item:** "No field/border validation." **Status: PROXY-VALIDATED across
border conditions on real public datasets; real Indian border footage remains the
open ask.** The pipeline has now been measured, with real numbers, on downloadable
datasets that each stress one real border condition (night, range, pattern-of-life,
dense daylight) — see the coverage matrix in §0. What cannot be faked, and is not,
is validation on *actual Indian border CCTV*: no such public data exists, so that
single step stays the honest ask, with the harness ready for the day it arrives.

> Nothing in this repository has been measured against real *Indian border*
> imagery at night, at range, in fog or rain. The proxy results below are on public
> surveillance datasets, labelled as proxies. No public dataset represents Indian
> border CCTV conditions; field validation on real border footage is required
> before any operational claim. — [limitations.md](limitations.md)

## 0. Border-condition proxy coverage (measured, real public data)

Each row is a real measured result on a real dataset, chosen to stress one border
condition. **These are proxies, explicitly not real Indian border footage.**

| Border condition | Proxy dataset (real) | Metric | Result | Script |
|------------------|----------------------|--------|--------|--------|
| Night / IR | Thermal-Person-Detector (CC-BY) | AP@0.5 / P / R | **0.29** / 0.88 / 0.27 | `benchmark_thermal_detection.py` |
| Range / far-field perimeter | VisDrone2019-DET (aerial) | AP@0.5 / P / R | **0.30** / 0.92 / 0.24 | `benchmark_visdrone_detection.py` |
| Open-border pattern-of-life | UCSD Ped2 (Street Scene protocol) | frame-AUC / RBDC / TBDC | **0.907** / 0.648 / 0.612 | `benchmark_streetscene_protocol.py` |
| Daylight person tracking | MOT17 | MOTA / IDF1 | **0.40–0.44** / 0.51–0.56 | `eval_mot_tracking.py` |
| Generic real-footage detection | Pexels street clip | person detections | 2,898 on one clip | `test_real_clips.py` |

**Consistent, honest finding across the hard conditions (night + range):** the
COCO/RGB detector is **high-precision but low-recall** — confident when it fires
(~0.9 precision) but missing most small/distant/thermal targets (~0.25 recall). It
is usable as a weak detector and clearly needs domain-specific models (thermal, and
higher-resolution/closer optics at range) to lift recall. That is exactly what a
real border pilot would quantify per site.

**What the proxies do NOT establish:** real Indian border performance at night, at
range, in fog/rain, on decade-old fog-lensed domes, with local class confusion
(livestock). That needs real footage — §1.

---

## 1. The ask (what only the sponsor can provide)

**Annotated footage from real border CCTV**, ideally covering the hard cases:

- **Night / low-light / IR** (the real border-surveillance condition).
- **Range** (subjects at the far field of a perimeter dome).
- **Weather** (fog, rain, haze, dust).
- **Open-border traffic** (Indo-Nepal / Indo-Bhutan lawful daily movement) for the
  pattern-of-life / normalcy path.
- **Livestock and vehicles**, for class-confusion measurement.

One SSB/CIBMS feed or a single pilot camera with a few annotated hours unblocks
this. It is a data-access and terms question for the sponsoring organisation, not
a tooling question. VIRAT is set up but gated behind a signed Data Protection
Agreement, which is the sponsor's to accept — see
[data-strategy.md](data-strategy.md).

## 2. What is already built and waiting

| Piece | Where | Ready |
|-------|-------|-------|
| Detection + tracking metrics (MOTA, IDF1, MOTP, ID-switches, MT/ML) | `prahari/eval/tracking.py`, `scripts/eval_mot_tracking.py` | ✅ |
| MOT-format footage adapter (real frames + real GT) | `prahari/eval/mot.py` (`MotSequenceSource`) | ✅ |
| Full-pipeline evaluation → HTML report | `scripts/evaluate.py --mot <dir>` | ✅ |
| Camera-capability profiling on real optics | `prahari/edge/profiling/` | ✅ |
| Normalcy / anomaly path (proxy-validated on real footage) | `scripts/validate_normalcy_streetscene_proxy.py` | ✅ |
| GPU inference on the production ONNX path | `scripts/benchmark_gpu_detectors.py` | ✅ |

The harness already produces real figures on real annotated footage — it does so
today on MOT17 (daylight street). Border footage drops into the **same** adapter.

## 3. Data format the harness expects (MOTChallenge)

Deliver each annotated clip as a standard MOT sequence directory:

```
<sequence_name>/
├── seqinfo.ini        # imWidth, imHeight, seqLength, imDir=img1, imExt=.jpg, frameRate
├── img1/              # 000001.jpg, 000002.jpg, ... (frames)
└── gt/gt.txt          # frame,id,bb_left,bb_top,bb_width,bb_height,conf,class,visibility
```

- `class` follows MOT conventions (1 = pedestrian); vehicle/livestock classes can
  be added and mapped in `prahari/edge/detect/onnx_yolo.py` (`COCO_TO_CLASS`).
- `visibility` (0–1) lets the scorer ignore heavily-occluded boxes.
- This is the exact format MOT17 ships in, so any MOT-annotated border footage
  works with no code change; other annotation formats need only a small converter
  to this layout.

## 4. Run it (once footage exists)

```bash
# Detection + tracking metrics on one or many annotated border sequences:
python scripts/eval_mot_tracking.py --model models/yolox_s.onnx --device cuda \
       --root data/testing/border --seqs BORDER-NIGHT-01,BORDER-FOG-02

# Full-pipeline evaluation -> var/eval/evaluation.html (detection, latency,
# alert suppression, profiling accuracy, per-class detection vs GT):
python scripts/evaluate.py --mot data/testing/border
```

## 5. Acceptance criteria (proposed, to agree with the sponsor)

These are targets to set *before* looking at results, so the outcome is honest.

| Condition | Metric | Proposed target |
|-----------|--------|-----------------|
| Night / IR person detection | recall @ IoU 0.5 | ≥ 0.60 (vs a stated daylight baseline) |
| Range (far-field perimeter) | recall on GT boxes < 40 px tall | ≥ 0.40 |
| Tracking identity | IDF1 | ≥ 0.50 |
| Class confusion | livestock-as-person false-positive rate | ≤ 5% |
| Open-border normalcy | false-alert load reduction on lawful traffic | ≥ 70% (with anomalies retained) |
| Latency | end-to-end per-frame on the target edge accelerator | real-time at the deployed stream rate |

A result **below** target is a finding, not a failure to hide: it tells the
sponsor exactly where a thermal model, a domain fine-tune, or a mounting change is
needed. That is the point of measuring.

## 6. If results are weak (the expected first outcome)

Prahari is honest that an RGB model on a night/thermal feed is a compromise
([roadmap item 5](../README.md#roadmap)). The likely field findings and their
fixes:

- **Night recall low** → thermal-specific model (see
  [thermal-validation.md](thermal-validation.md)) or IR-illuminated capture.
- **Range recall low** → higher input resolution (yolov8m@1280 is measured and
  real-time-capable on GPU) or a longer-focal-length camera at that post.
- **Class confusion** → a short domain fine-tune on the sponsor's own annotated
  clips, using the same harness to measure the gain.

## 7. Honest statement for evaluators

> Give us one real border feed and a short pilot, and the field-validation gap
> closes. The harness, the adapter, the metrics and the acceptance criteria are
> built and produce real figures the moment annotated border footage exists.
> Until then, every simulated result in this repository is labelled a
> demonstration, not field performance.

Related: [evaluation.md](evaluation.md), [validation-matrix.md](validation-matrix.md),
[limitations.md](limitations.md), [thermal-validation.md](thermal-validation.md).
