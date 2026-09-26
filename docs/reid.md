# Cross-Camera Re-Identification

Prahari uses a cross-camera coordinator to maintain a single global identity for objects moving across the field of view of multiple cameras. This depends on an appearance cue to re-identify entities across different camera feeds.

## Dataset and Protocol
The model is trained on **Market-1501** bounding box crops. The dataset protocol requires:
- **Disjoint train and test identities**: The identities in the training set are completely distinct from those in the test set.
- **Genuine Cross-Camera Protocol**: The evaluation protocol explicitly drops same-identity/same-camera pairs from the gallery, forcing a correct match to come from a different camera. This evaluates genuine cross-camera matching capability.
- **Held-out validation**: A portion of training identities was isolated for a validation split during training.

## Baseline
The baseline cue was a simplistic, normalized top-and-bottom HSV histogram. Measured against the Market-1501 dataset, the baseline performed poorly on true cross-camera tests:
- **Rank-1 Accuracy**: 0.0975
- **Mean Average Precision (mAP)**: 0.0304

## Architecture and Training
- **Backbone**: ImageNet-pretrained ResNet-18 (a lightweight alternative to heavier networks, producing a 128-d embedding).
- **Loss**: Identity classification loss combined with a batch-hard triplet loss to directly optimize the distance structure.
- **Training Configurations**: Trained with mixed augmentations (random erasing, color jitter, cropping) to model real-world variations between camera feeds (e.g., exposure shifts, approach directions).
- **Deployment**: The model is exported to ONNX format (45 MB) and executed via ONNX Runtime without PyTorch dependencies, keeping the node deployment compact.

## Metrics and Acceptance
The model was empirically evaluated against the HSV histogram baseline using the `evaluate_reid_market.py` script. The learned embedding strictly outperformed the baseline.

| Cue | Rank-1 | mAP | ms/crop (latency) |
| --- | --- | --- | --- |
| HSV Histogram (CPU) | 0.0883 | 0.0280 | 2.21 |
| ResNet-18 Learned Embedding (CUDA) | 0.7050 | 0.4913 | 12.55 |
| **OSNet x1_0 (CUDA)** | **0.9474** | **0.8453** | — |

*(ResNet-18 vs HSV: Rank-1 +0.6167, mAP +0.4633. OSNet vs ResNet-18: Rank-1
+0.2424, mAP +0.3540 — SOTA level.)*

**Acceptance Decision (updated 2026-09-26).** The ResNet-18 embedding was the
original production choice over the HSV baseline. **OSNet x1_0 with the correct
torchreid Market-1501 checkpoint now reaches Rank-1 0.947 / mAP 0.845** — SOTA and
a large jump over ResNet-18 — and is the recommended production Re-ID.
`scripts/benchmark_reid_osnet_correct.py` reproduces the number;
`scripts/export_reid_osnet.py` exports `models/reid_osnet_market.onnx`, a drop-in
for the production `ReidEmbedder` (same 256×128 + ImageNet-norm preprocessing).

> **Root-cause note.** An earlier OSNet attempt measured Rank-1 0.14 because the
> shipped `osnet_x1_0_market1501.pth` was actually the **ImageNet backbone**
> (classifier 1000, not Market's 751). The fix was fetching the correct checkpoint;
> the pipeline and preprocessing were already right.

## Integration Details
- **Dynamic Loading**: The system automatically attempts to load `models/reid.onnx` via the `ReidEmbedder`. If absent, it seamlessly falls back to the HSV histogram.
- **Confidence Gating**: We enforce a detection confidence gate (`>= 0.45`) before extracting the signature to prevent poor detections from contaminating the embedding. 
- **Distance Metric**: The normalized embedding is scored using cosine similarity mapped to `[0, 1]` to adhere to the existing coordinator logic.

## Limitations (REAL MEASURED vs UNVALIDATED)
> [!WARNING]
> **UNVALIDATED / DOMAIN-SPECIFIC**
> The dataset consists solely of daylight RGB images. While the model trains genuine cross-camera matching between similar sensors, it **does not** address visible-to-thermal matching. A thermal or night-IR camera will fall back to timing and tracking data, and the model does not attempt cross-modal Re-ID.
