# Prahari Validation Matrix

This matrix provides an honest, measured account of the current capabilities, distinguishing between features that have been strictly validated on public/real datasets versus those simulated or awaiting field data.

| Capability | Implementation Status | Real Validation | Simulated Validation | Benchmark | Limitation | Current Maturity |
|---|---|---|---|---|---|---|
| **Detector (YOLOX-S)** | IMPLEMENTED | MOT17 (Held-out seqs 02, 04) | N/A | 12.6 fps, 79.4 ms, MOTA 0.403 | Real-time tracking requires modern GPU/Edge accelerator | Prototype-validated |
| **Cross-Camera Re-ID (ResNet)** | IMPLEMENTED | Market-1501 | N/A | Rank-1: 0.705, mAP: 0.485 | Domain shift to actual border CCTV required | Experimentally validated |
| **Cross-Camera Re-ID (HSV)** | IMPLEMENTED | Market-1501 | N/A | Rank-1: 0.097, mAP: 0.030 | Color-based only; fails with similar clothing | Deployment-ready fallback |
| **Segmentation (GrabCut)** | IMPLEMENTED | MOT17-04 | N/A | 87.5 ms (11.4 fps) | Bleeds on cluttered backgrounds | Deployment-ready architecture |
| **Segmentation (SAM 2)** | IMPLEMENTED | MOT17-04 | Synthetic (IoU: 0.975) | 421.2 ms (2.4 fps) | Heavy GPU requirement, too slow for real-time edge | Prototype-validated |
| **Tracking (ByteTrack Logic)** | IMPLEMENTED | MOT17 | N/A | IDF1 0.500 | Assumes consistent FPS | Prototype-validated |
| **Alert Governor** | IMPLEMENTED | N/A | Scenarios (`tests/test_patrol.py`) | N/A | Depends on accurate zone configuration | Deployment-ready architecture |
| **Patrol Suppression** | IMPLEMENTED | N/A | Route Deviation Tests | N/A | Requires manual input of patrol profiles | Deployment-ready architecture |
| **Offline Sync / Integrity** | IMPLEMENTED | Local DB / Network Outage Tests | N/A | Queue/Hash chain verification passed | Storage constrained by edge disk size | Deployment-ready architecture |
| **ANPR (LPRNet/EasyOCR)** | IMPLEMENTED | N/A | Capability Gating Tests | N/A | Heavily dependent on camera mounting angle | Requires field validation |
| **Tamper Detection** | IMPLEMENTED | N/A | Lens cover/disconnect simulation | N/A | Cannot distinguish perfect physical replicas | Experimentally validated |
| **Visible↔Thermal Re-ID** | UNVALIDATED | N/A | N/A | N/A | Lacking cross-modal dataset | UNVALIDATED |
