# Prahari — Thermal & Cross-Modal Re-ID Validation

**Audit item:** "Thermal/Re-ID validation." **Status: thermal detection MEASURED;
cross-modal Re-ID stress-tested + true-protocol harness ready (gated data pending).**
All results here are on **generic, non-Indian** datasets and are labelled as such;
Indian border-specific thermal/Re-ID validation remains pending representative
field data. No fabricated numbers.

Real border night capability is thermal, and an RGB model on a thermal feed is a
compromise Prahari states openly. This document reports what was measured, with a
pre-trained model on real data, and what is honestly still gated.

---

## 1. Two distinct questions (do not conflate)

1. **Thermal detection** — does person detection work on a real thermal (LWIR)
   feed? → **Measured.**
2. **Visible↔thermal Re-ID** — can a person on a daytime optical camera be
   re-identified on a night thermal camera? → **Stress-tested now; true LWIR
   number needs a gated paired dataset (harness ready).**

---

## 2. Thermal detection — MEASURED (real LWIR data)

Dataset: **Thermal-Person-Detector** (Voxel51/`dgural` on Hugging Face; from
Roboflow SMART2), **CC-BY-4.0**, 8,778 real thermal images with person boxes;
evaluated on the held-out **test split**. Detector: `models/yolox_s.onnx` (COCO,
RGB-trained, **no thermal training**). Harness:
`scripts/benchmark_thermal_detection.py`.

| Metric | Result (full 854-image test split) |
|--------|--------|
| **AP@0.5** | **0.289** |
| precision@0.35 | **0.881** |
| recall@0.35 | **0.268** |

**Honest reading:** this is **partial transfer**, not a free ride. The RGB-trained
detector is **confident when it fires (precision 0.88) but misses most people
(recall 0.27)** on thermal, for AP@0.5 0.29. So an RGB model is usable as a weak
thermal detector but leaves ~3/4 of people undetected — the domain gap, quantified.
**A thermal-specific model is needed to lift recall**; that is the whole point of
measuring rather than assuming. (Generic dataset, not Indian.)

## 3. Cross-modal Re-ID

### 3a. Colour-removal STRESS TEST (done) — not true LWIR

`scripts/benchmark_crossmodal_reid.py` on Market-1501: the production visible Re-ID
model, with the query's colour removed (grayscale) against a visible gallery.

| Query → visible gallery | Rank-1 | mAP |
|-------------------------|--------|-----|
| Visible (baseline) | 0.705 | 0.491 |
| Colour-removed (stress test) | **0.057** | 0.039 |
| Gap | **−0.648** | −0.453 |

This is a **stress test, not true LWIR**: it removes the colour cue (which LWIR
also lacks) but does not reproduce real thermal texture/appearance. It shows the
visible model collapses without colour — a lower bound on the modality gap.

### 3b. True RGB↔LWIR Re-ID (RegDB protocol) — harness ready, data gated

`scripts/benchmark_regdb_vireid.py` implements the **real** RegDB protocol —
visible→thermal and thermal→visible Rank-1/mAP over the standard idx trials on
**actual paired visible+thermal images of the same identities**. It is built and
validated (it correctly refuses incomplete data) and runs the instant a complete
RegDB (or SYSU-MM01) is present.

**Why it is not run here:** the standard paired VI-ReID datasets are **research-
gated** — RegDB (copyright form / email `mangye16@gmail.com`) and SYSU-MM01
(agreement); there is no open-license VI-ReID person dataset, and the only free
mirror (HF `Demonz43/Reg_DB`) is **visible-only**, which the harness rejects. This
is a data-access gate, not a tooling gap.

**To produce the true number:** obtain RegDB/SYSU under their terms, then:

```bash
python scripts/benchmark_regdb_vireid.py --root <RegDB with Visible+Thermal+idx>
```

The honest wording it enables: *"True RGB–LWIR cross-modal Re-ID was evaluated on a
paired visible/thermal benchmark (RegDB)."* Raising the numbers further is a
roadmap step — train a VI-ReID model (e.g. AGW/DDAG) on RegDB/SYSU rather than
using the visible-only embedder.

## 4. India relevance — KKWETC thermal FACE domain (harness ready, gated)

`scripts/benchmark_kkwetc_thermal_face.py` runs the deployable face detector
(YuNet) on the **KKWETC Indian thermal face database** (816 visible / 150 thermal
images, FLIR C2, captured in India) and reports the thermal-face detection rate.

**Scope, not overstated:** KKWETC is an Indian thermal **face-recognition** dataset,
**not** an Indian pedestrian RGB↔LWIR surveillance Re-ID dataset. It supports the
claim *"India-specific thermal **face-domain** validation"* and must **not** be read
as *"Indian border thermal Re-ID validated."* It is research-gated (request from the
authors — "'KKWETC' Indian Face Database", IJETT 2017); the harness prints the
access procedure and runs when the data is supplied.

## 5. What is built vs. what is pending

| Piece | Status |
|-------|--------|
| Thermal person detection on real LWIR | **MEASURED** (`benchmark_thermal_detection.py`) |
| Cross-modal colour-removal stress test | **MEASURED** (`benchmark_crossmodal_reid.py`) |
| True RegDB visible↔thermal Re-ID | **HARNESS READY**, gated data pending (`benchmark_regdb_vireid.py`) |
| India thermal face (KKWETC) | **HARNESS READY**, gated data pending (`benchmark_kkwetc_thermal_face.py`) |
| Re-ID embedding backend (swap for a VI-ReID model) | ready (`prahari/edge/crosscam/reid.py`) |

## 6. Honest statement for evaluators

> Thermal is the real border-night modality, and Prahari does not pretend an RGB
> model closes it — measured AP@0.5 0.29 (precision 0.88, recall 0.27) on real
> thermal shows partial transfer with most people missed, quantifying the gap. Cross-
> modal Re-ID is stress-tested (visible model collapses without colour, 0.705 →
> 0.057) and the true RegDB visible↔thermal harness is built and waiting on the
> gated paired dataset. India relevance is a ready KKWETC thermal-face harness,
> scoped honestly as face-domain. Every number here is on generic data and
> labelled non-Indian; Indian border thermal/Re-ID validation needs representative
> field data or the gated benchmarks, which is the stated ask.

Related: [reid.md](reid.md), [crosscam.md](crosscam.md),
[validation-matrix.md](validation-matrix.md),
[field-validation-kit.md](field-validation-kit.md).
