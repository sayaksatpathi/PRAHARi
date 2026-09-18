# Model Provenance and License Audit

This document tracks the provenance, licensing, and expected deployment characteristics of all external AI models used within Prahari.

> [!WARNING]
> This is a technical provenance log, not a legal procurement conclusion. Items here must be verified by the relevant open-source compliance or procurement team before public or commercial deployment.

## 1. YOLOX (Detection)
- **Role**: Primary edge detector (Person/Vehicle localization).
- **Variants Used**: YOLOX-Tiny @ 640, YOLOX-S @ 640.
- **Source**: Megvii Technology (https://github.com/Megvii-BaseDetection/YOLOX).
- **License**: Apache License 2.0.
- **Intended Use**: General object detection. Safe for commercial/proprietary integration without copyleft contamination.
- **Modification State**: Exported to ONNX. No fundamental architecture changes.

## 2. ByteTrack / BoT-SORT (Tracking Logic)
- **Role**: Multi-object tracking across sequential frames.
- **Source**: https://github.com/ifzhang/ByteTrack
- **License**: MIT License.
- **Intended Use**: Tracking by detection. Safe for proprietary use.
- **Modification State**: Reimplemented natively in Python/NumPy within the pipeline to avoid heavy framework dependencies.

## 3. ResNet-18 (Re-Identification)
- **Role**: Cross-camera person matching (Extracting 128-D embeddings).
- **Source**: TorchVision (base architecture) trained on Market-1501.
- **License**: BSD 3-Clause (for TorchVision base). The Market-1501 dataset used for training is strictly for *academic research purposes*.
- **Intended Use**: Extracted embeddings for feature similarity.
- **Modification State**: Custom trained checkpoint exported to ONNX.
- **Compliance Note**: While the model architecture is permissively licensed, the weights were learned on Market-1501. If the system is deployed beyond research or internal government evaluation, a commercially clear Re-ID dataset should be used to retrain the weights.

## 4. SAM 2 (Segment Anything Model 2)
- **Role**: High-fidelity pixel-mask generation for critical event evidence.
- **Source**: Meta AI (https://github.com/facebookresearch/segment-anything-2).
- **License**: Apache License 2.0.
- **Intended Use**: Zero-shot promptable segmentation. Safe for commercial use.
- **Modification State**: ONNX export of the model; no structural changes. Runs only on CUDA-capable edge nodes due to latency constraints.

## 5. LPRNet / EasyOCR (ANPR)
- **Role**: License plate text recognition.
- **Source**: Custom/Open Source implementations (EasyOCR relies on JaidedAI).
- **License**: Apache License 2.0 (EasyOCR).
- **Intended Use**: Text extraction from bounded plates.
- **Modification State**: Integrated with temporal aggregation to suppress single-frame hallucinations.

## 6. Face Detection — SCRFD-500M (CANDIDATE) + Haar (baseline)
- **Role**: Face detection component. The OpenCV Haar frontal-face cascade
  (`prahari/edge/detect/face.py`, ships with opencv-python, no external weights)
  remains the **unchanged baseline**. SCRFD-500M is added as a separate ONNX
  backend (`prahari/edge/detect/scrfd.py`) behind the same interface and is
  classified **CANDIDATE** — not promoted.
- **Model filename**: `models/scrfd_500m.onnx` (2.30 MB, not committed — gitignored).
- **Source URL / repository**: `https://huggingface.co/RuteNL/SCRFD-face-detection-ONNX` (file `500m.onnx`); upstream architecture/weights: InsightFace SCRFD (https://github.com/deepinsight/insightface, `detection/scrfd`).
- **Checksum (SHA-256)**: `72ce8732254ed6678d78e8a8c4b8bf2d4258128afe714edec877eaa90f7b7958` (verified on download 2026-09-18).
- **License**: InsightFace **code** is MIT. InsightFace **pretrained models are stated for non-commercial / research use only** — this is the binding restriction for the weights.
- **Provenance / restrictions**: community ONNX re-export of the official InsightFace SCRFD-500M. Treat as **research/evaluation only** until weights with clear commercial terms are obtained or the model is retrained on a commercially clear dataset. Same class of restriction as the Re-ID weights.
- **Input size & preprocessing**: fixed **640×640**; letterbox resize preserving aspect ratio into a 640×640 zero-padded canvas; blob = `(pixel − 127.5)/128`, **BGR→RGB**, NCHW; boxes decoded via distance-to-bbox at strides 8/16/32 (2 anchors/cell), NMS IoU 0.4, then rescaled by the letterbox factor to original coordinates.
- **Inference provider**: ONNX Runtime, **CUDA if available else CPU** (auto). No training framework added.
- **Measured (WIDER FACE val, VOC AP@0.5)**: see [benchmark-matrix.md](benchmark-matrix.md) — recorded from an actual run, not asserted here.
- **Status**: **CANDIDATE — not promoted.** The Haar cascade remains the production/default face detector. SCRFD is enabled only for controlled evaluation/demo via `build_face_detector("scrfd")` (`prahari/edge/detect/face.py`); nothing in the default pipeline path selects it.
- **Promotion gate (must all hold before SCRFD becomes the default):**
  1. **Weight licensing resolved.** The InsightFace pretrained weights are non-commercial/research-only. Promotion requires either (a) procurement/clearance of these weights for the intended deployment, or (b) retraining/replacing the weights with a commercially clear source.
  2. **Re-validate on any source change.** If the model file, weights, or export source changes, re-run `scripts/evaluate_widerface.py --detector scrfd` on the WIDER FACE val split and update the benchmark matrix before promoting.
  3. **No GPU claim without measurement.** ORT CUDA EP did not load in the current environment (cuDNN 9.x missing); a GPU latency/FPS figure may only be stated after an actual CUDA-provider run.
  > Until all three hold, promoting the research-only weights into the release default would be premature. For the SIH demo, present SCRFD as an *experimental candidate* alongside the measured Haar-vs-SCRFD comparison.

## Summary of Risk
The AI stack is built almost entirely on **Apache 2.0** and **MIT** licensed architectures, making it highly suitable for enterprise and government deployment without GPL/copyleft risks. The areas requiring compliance review are the Re-ID weights (Market-1501 dataset terms) and the **SCRFD-500M weights (InsightFace non-commercial/research-only model terms)** — the SCRFD architecture/code is MIT, but its pretrained weights are not cleared for commercial use.
