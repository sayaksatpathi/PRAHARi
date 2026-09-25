# Prahari — Thermal & Cross-Modal Re-ID Validation

**Audit item:** "Thermal/Re-ID validation." **Status: OPEN — blocked on a paired
visible↔thermal dataset, not on tooling.** Real border night capability is
thermal, and an RGB model on a thermal feed is a compromise Prahari states openly
([README roadmap item 5](../README.md#roadmap)). This document defines exactly
what validation is owed, what is already built to run it, and the honest ask.

---

## 1. Two distinct questions

Do not conflate them:

1. **Thermal detection** — does person/vehicle detection work on a *thermal*
   (LWIR/IR) feed at all? An RGB-trained detector on thermal imagery is a domain
   mismatch and is expected to degrade.
2. **Visible↔thermal Re-ID** — can a person seen on a daytime *optical* camera be
   re-identified on a night *thermal* camera (cross-modal)? This is strictly
   harder than same-modality Re-ID and is the real cross-camera night problem.

## 2. Current honest state

| Capability | Status | Evidence |
|-----------|--------|----------|
| Same-modality Re-ID (optical, Market-1501) | **MEASURED** | ResNet-18, Rank-1 0.705 / mAP 0.485 (`scripts/evaluate_reid_market.py`) |
| Thermal detection | **UNVALIDATED** | no thermal footage on hand |
| Visible↔thermal Re-ID | **UNVALIDATED** | no cross-modal dataset — [validation-matrix.md](validation-matrix.md) |

Cross-camera appearance matching today uses an HSV colour signature, which is
weak between cameras of different optics and **meaningless across modalities**
(a thermal image has no visible colour) — see [crosscam.md](crosscam.md). So
cross-modal Re-ID needs a proper model *and* a paired dataset; neither the model
nor the data is claimed here.

## 3. The ask (what only the sponsor / a dataset provides)

- **Thermal detection:** an annotated thermal (LWIR) clip set, MOT format
  ([field-validation-kit.md](field-validation-kit.md) §3). Public option to pursue
  under terms: **FLIR ADAS thermal**, **KAIST multispectral pedestrian** (paired
  RGB+thermal), **LLVIP** (visible-infrared paired).
- **Cross-modal Re-ID:** a paired **visible↔thermal person Re-ID** dataset with
  identity labels across modalities — e.g. **SYSU-MM01** or **RegDB** (both gated
  by academic agreements). These are the standard benchmarks for VT-Re-ID.

None of these are on disk; obtaining them under the appropriate terms is the
sponsoring organisation's / a research agreement's to do.

## 4. What is already built and waiting

| Piece | Where | Ready |
|-------|-------|-------|
| Detection metrics on MOT-format footage (works on thermal frames too) | `scripts/eval_mot_tracking.py` | ✅ |
| Re-ID evaluation harness (Rank-1 / mAP, query/gallery) | `scripts/evaluate_reid_market.py`, `scripts/evaluate_reid.py` | ✅ |
| ONNX Re-ID embedding backend (swap the model, keep the harness) | `prahari/edge/crosscam/reid.py` | ✅ |
| GPU inference on the production ONNX path | `scripts/benchmark_gpu_detectors.py` | ✅ |

The Re-ID harness computes Rank-1/mAP against a labelled query/gallery. A
cross-modal dataset in that layout (query = thermal, gallery = visible, shared
identities) runs through it unchanged; a VT-Re-ID model then replaces the
embedding backend behind the same interface.

## 5. Thermal-detection proxy (available now, clearly labelled)

Without thermal footage, a *lower-bound* sanity check of the pipeline on
thermal-like imagery can be produced by evaluating the existing detector on
grayscale/contrast-inverted frames (a crude thermal analogue). This is **not** a
thermal accuracy claim — a real LWIR sensor's texture, halo and polarity differ —
it only confirms the pipeline ingests and scores single-channel/inverted frames
without breaking, and gives a pessimistic floor. It must be reported as a proxy,
never as thermal performance. (The honest number is the one measured on real
thermal footage via §4.)

## 6. Acceptance criteria (proposed, agree before running)

| Question | Metric | Proposed target |
|----------|--------|-----------------|
| Thermal person detection | recall @ IoU 0.5 on thermal GT | ≥ 0.50 with an RGB model; a thermal-specific model is the fix if below |
| Visible↔thermal Re-ID | Rank-1 on SYSU-MM01 / RegDB | report honestly vs published VT-Re-ID baselines; no target invented here |

## 7. Honest statement for evaluators

> Thermal is the real border-night modality and Prahari does not pretend an RGB
> model closes it. The detection and Re-ID harnesses are built and will produce
> real thermal and cross-modal numbers the moment a paired visible↔thermal
> dataset is available under appropriate terms. Until then this is labelled
> UNVALIDATED, and a thermal-specific model is a named roadmap item, not a hidden
> assumption.

Related: [reid.md](reid.md), [crosscam.md](crosscam.md),
[validation-matrix.md](validation-matrix.md),
[field-validation-kit.md](field-validation-kit.md).
