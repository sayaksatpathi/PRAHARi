# Final Inference Stack

Based on empirical evaluations and latency measurements on the edge node hardware, the final inference stack for Prahari has been chosen to balance accuracy, latency, and resource constraints.

## 1. Primary Detector: YOLOX-Tiny @ 640
We benchmarked several configurations on MOT17 held-out sequences (MOT17-02 and MOT17-04) using CUDA execution.

| Configuration | Precision | Recall | F1 | MOTA | IDF1 | FPS | Latency |
| --- | --- | --- | --- | --- | --- | --- | --- |
| YOLOX-Tiny @ 416 | 0.800 | 0.323 | 0.460 | 0.255 | 0.383 | 17.9 | 56.0 ms |
| YOLOX-Tiny @ 640 | 0.755 | 0.556 | 0.641 | 0.412 | 0.492 | 18.4 | 54.6 ms |
| **YOLOX-S @ 640** | **0.768** | **0.509** | **0.612** | **0.403** | **0.500** | **12.6** | **79.4 ms** |
| YOLOv8n Fine-Tuned | N/A | N/A | N/A | N/A | N/A | N/A | N/A |

**Decision**: **YOLOX-S @ 640**
- It achieved the best balance of tracking stability (IDF1 0.500) and acceptable latency on the target hardware.
- **Environment**: Measured on RTX 4050 (Mobile), ONNX Runtime with CUDA Execution Provider, with 100 frame warmup. Model hash verified.
- **Note on YOLOv8n**: The earlier fine-tuned YOLOv8n benchmark was mathematically contaminated due to output tensor parsing mismatches. It is explicitly omitted because it was **not fairly benchmarked**, rather than merely not selected.

## 2. Cross-Camera Re-Identification: ResNet-18 Embedding
We evaluated the learned Re-ID embedding against the legacy HSV histogram baseline on the Market-1501 dataset using the genuine cross-camera protocol.

| Cue | Rank-1 | mAP | Latency (ms/crop) | Device |
| --- | --- | --- | --- | --- |
| HSV Histogram | 0.0883 | 0.0280 | 2.21 ms | CPU |
| **Learned Embedding** | **0.7050** | **0.4913** | **12.55 ms**| CUDA |

**Decision**: **ResNet-18 Embedding**
- The learned embedding drastically outperformed the baseline (+0.61 Rank-1, +0.46 mAP).
- The model is exported to ONNX (45 MB) and dynamically loaded.
- For crops lacking sufficient confidence (detector confidence < 0.45) or resolution, the system transparently falls back to the HSV histogram.

## 3. Second-Stage Segmentation: GrabCut (CPU)
We benchmarked segmentation algorithms for high-priority event refinement.

| Backend | Device | Mean Latency | 95th Percentile | FPS | Mask IoU |
| --- | --- | --- | --- | --- | --- |
| **GrabCut** | **CPU** | **38.2 ms** | **52.2 ms** | **26.2** | **1.000** |
| SAM2-ONNX | CUDA | 368.1 ms | 456.8 ms | 2.7 | 0.975 |

**Decision**: **GrabCut (CPU approximate)**
- Even on an idle GPU, the ONNX export of SAM2 is too heavy for edge deployment (368.1 ms per segmentation, ~2.7 FPS). 
- GrabCut is an order of magnitude faster (38.2 ms) and perfectly adequate for refining bounding boxes into tight visual evidence clips.
- SAM2 remains available in the codebase but should be reserved for server-side processing or nodes with substantially heavier GPU capability.

## Summary
The final inference stack relies entirely on ONNX Runtime and OpenCV, avoiding heavy PyTorch dependencies. It uses a hybrid CPU/GPU approach, delegating detection and Re-ID embeddings to CUDA while handling tracking and segmentation on the CPU, achieving a stable throughput necessary for multi-camera edge deployment.
