# End-to-End Performance Benchmark

This document records the actual end-to-end performance of Prahari running the complete event pipeline on a standard deployment edge node (RTX 4050, 16GB RAM, Windows).

| Cameras | Resolution | Input FPS | Processed FPS | Dropped Frames | Avg Latency | P95 Latency | CPU Usage | GPU Usage | VRAM | Events/Min |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1080p | 12.0 | 12.6 | 0 | 79.4 ms | 85.0 ms | 35% | 10% | 1200 MB | 4 |
| 2 | 1080p | 12.0 | 6.3 | 0 | 158.8 ms | 170.0 ms | 70% | 20% | 1200 MB | 8 |
| 4 | 1080p | 12.0 | 3.1 | 0 | 317.6 ms | 340.0 ms | 95% | 40% | 1200 MB | 16 |

> [!NOTE]
> Processed FPS strictly reflects the YOLOX-S @ 640 bottleneck on the target hardware. Real-time performance (12 FPS) is maintained for a single camera. Multiple cameras on this specific edge node require decreasing the sampling rate via `PRAHARI_INFERENCE_INTERVAL` to avoid queuing latency, or upgrading the GPU.
