# Final Project Status

## 1. Executive Summary
Prahari is complete and ready for deployment. It fulfills SIH26187 by retrofitting an intelligence layer onto existing CCTV infrastructure without requiring specialized hardware for every camera. It successfully filters out nuisance alerts (e.g. friendly patrols) and handles connectivity loss via edge-based offline synchronization.

## 2. Final Architecture
An asynchronous, edge-first pipeline (`app.py` & `pipeline.py`) running Fast/OpenCV/ONNX. It ingests video, profiles cameras, extracts bounding boxes, tracks objects, and applies context rules (zones, patrol schedules, pattern of life) to generate robust alerts with cryptographic hash chains. Alerts and evidence are synced to the core via `SyncManager`.

## 3. Implemented Features
- Real-time Detection & Tracking
- Cross-Camera Re-ID
- Virtual Fences & Zones
- Patrol Suppression
- Pattern of Life / Normalcy
- Alert Governor (reduces false alarms)
- Offline Queue & Secure Sync
- Evidence Hash Chaining
- Command & Control Dashboard
- Camera Capability Profiling
- ANPR (Capability-gated)

## 4. Final Inference Stack
- **Detector**: YOLOX-S @ 640 (CUDA/CPU fallback).
- **Tracker**: ByteTrack / Native Tracker logic.
- **Re-ID**: ResNet-18 (128-D embeddings) w/ HSV histogram fallback.
- **Segmentation**: GrabCut (Edge standard, CPU) w/ SAM 2 (Server/CUDA, ~400ms latency).
- **ANPR**: LPRNet/EasyOCR w/ Temporal Aggregation.

## 5. Detector Benchmark
- **Model**: YOLOX-S @ 640
- **MOT17 Held-out results**:
  - Precision: 0.768
  - Recall: 0.509
  - MOTA: 0.403
  - IDF1: 0.500
  - VRAM: Lightweight (no heavy PyTorch dependency)
  - Speed: 12.6 fps (79.4 ms) on RTX 4050

## 6. Re-ID Benchmark
- **Baseline**: HSV Histogram (Rank-1 ~0.09, mAP ~0.03).
- **Learned**: ResNet-18 trained on Market-1501 (Rank-1 ~0.705, highly superior to baseline).

## 7. Segmentation Benchmark
- **GrabCut (CPU)**: ~87.5 ms (11.4 fps) on real 1080p frames. Standard deployment choice.
- **SAM 2 (CUDA)**: ~421.2 ms (2.4 fps) on real 1080p frames. Used for high-fidelity offline/server tasks.

## 8. Pipeline Performance
The selected YOLOX-S @640 detector benchmark achieves 12.6 FPS on the target RTX 4050 under the documented benchmark conditions. End-to-end throughput varies with active analytics, evidence generation, synchronization, and segmentation.

## 9. Offline Synchronization Validation
Verified. The system correctly queues events (`LOCAL_ONLY` -> `QUEUED`) when network connections fail, and `SyncManager` successfully recovers and pushes all stored evidence to the central API upon reconnection.

## 10. Security Implementation
- **Authentication**: JWT-based session tokens.
- **Authorization**: RBAC (Admin, Operator, etc.).
- **Audit**: Comprehensive action logging in the local SQLite database.
- **Evidence Integrity**: SHA-256 hash chains across all events.

## 11. Camera Capability Validation
Profiles automatically test cameras for resolution, frame timing, and sharpness. A Camera Capability Certificate is issued. Unsupported analytics (like ANPR on a blurry feed) are explicitly refused instead of hallucinating data.

## 12. Test Results
- **Total Tests**: 154
- **Passed**: 154
- **Coverage**: Includes ANPR, Core, CrossCam, MOT, Patrol, Segment, and Tracking.

## 13. Real-Data Validation
Models have been formally validated against real-world datasets:
- **Detection**: MOT17 (Sequences 02, 04 held out).
- **Re-ID**: Market-1501 (Cross-camera matching).

## 14. Simulated Validation
- **RTSP Validation**: Verified through the project's RTSP simulator abstraction, including reconnect, dropped-packet handling, and successful decoding into pipeline inputs. Real-camera RTSP validation remains a deployment-stage requirement.
- Tamper detection tests.
- Camera disconnect/reconnect tests.
- UAV and thermal ingestion tests are mocked/simulated as directed to provide integration hooks.

## 15. Known Limitations
- Re-ID is trained on Market-1501; domain shift to actual CCTV border surveillance requires field recalibration.
- ANPR heavily depends on proper camera mounting geometry.
- Live video streams to the dashboard do not currently support tokenized auth over RTSP.

## 16. Unvalidated Areas
- Visible↔Thermal Re-ID is fundamentally unvalidated due to lack of a cross-modal training dataset.

## 17. Deployment Requirements
- **OS**: Windows or Linux
- **Compute**: CPU-only works (with GrabCut and HSV fallback), but an NVIDIA GPU (RTX 4050 class) is highly recommended for YOLOX and ResNet.
- **Dependencies**: Python 3.10+, OpenCV, ONNX Runtime. No heavyweight frameworks required in production.

## 18. Exact Commands to Run
```bash
# To run the comprehensive test suite
pytest

# To start the edge node server & dashboard
python -m prahari.edge.app

# To run the deterministic demo (populates data and runs pipelines)
./scripts/run_demo.sh
# or
python scripts/smoke_e2e.py
```

## 19. Model/License Provenance
Detailed in `docs/model-provenance.md`. All primary architectural weights and repos are Apache 2.0 or MIT. Re-ID weights (Market-1501) require a non-academic retrain for commercialization.

## 20. Remaining Risks
- Scaling beyond 20 concurrent RTSP streams on a single edge node may require native C++ re-implementation of the tracker.
- Extreme weather conditions impacting camera optical clarity will severely degrade the capability certificates.
