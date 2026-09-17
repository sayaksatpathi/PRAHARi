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

## Summary of Risk
The AI stack is built almost entirely on **Apache 2.0** and **MIT** licensed architectures, making it highly suitable for enterprise and government deployment without GPL/copyleft risks. The only area requiring compliance review is the Re-ID weights (Market-1501 dataset terms).
