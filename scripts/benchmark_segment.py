"""Benchmark the segmentation backends: latency, throughput, and GPU memory.

    python scripts/benchmark_segment.py
    python scripts/benchmark_segment.py --backend grabcut --boxes 100

Reports what it can honestly measure on this machine. Latency and throughput are
always real. GPU memory is reported only when a CUDA execution provider and an
NVIDIA GPU are actually present - on a CPU-only install it says so rather than
inventing a figure, because "VRAM used by a backend that ran on the CPU" is not a
number that means anything.

The point of this benchmark for an RTX 4050 (6 GB) is to answer, before wiring
SAM into a live node, whether the chosen configuration fits in memory and runs
fast enough to be invoked on an event without stalling the pipeline. Because SAM
weights could not be downloaded on the build machine, the SAM row here will
usually be a load failure with a clear reason; the GrabCut row is real.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.common.config import get_settings  # noqa: E402
from prahari.common.models import BBox  # noqa: E402


def gpu_memory_mb() -> float | None:
    """Used GPU memory via nvidia-smi, or None if unavailable."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None
    try:
        out = subprocess.check_output(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=10)
        return float(out.strip().splitlines()[0])
    except Exception:
        return None


def make_scene(seed: int = 7):
    """A frame with a clear foreground object and a box around it."""
    import cv2

    rng = np.random.default_rng(seed)
    img = np.full((480, 640, 3), (60, 90, 70), np.uint8)
    img = (img + rng.normal(0, 6, img.shape)).clip(0, 255).astype(np.uint8)
    cv2.rectangle(img, (280, 150), (350, 380), (200, 180, 160), -1)
    cv2.circle(img, (315, 135), 22, (200, 180, 160), -1)
    box = BBox(x1=278, y1=112, x2=352, y2=378)
    return img, box


def bench_backend(name: str, segmenter, img, box, n: int) -> dict:
    if segmenter is None:
        return {"backend": name, "status": "not available"}

    segmenter.warmup()
    base_mem = gpu_memory_mb()
    latencies = []
    ok = 0
    for _ in range(n):
        t0 = time.perf_counter()
        r = segmenter.segment(img, box)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if r is not None:
            ok += 1
    peak_mem = gpu_memory_mb()

    lat = np.array(latencies)
    result = {
        "backend": segmenter.describe().get("name", name),
        "device": segmenter.describe().get("device", "cpu"),
        "approximate": segmenter.describe().get("approximate", False),
        "runs": n,
        "succeeded": ok,
        "latency_ms_mean": round(float(lat.mean()), 1),
        "latency_ms_p50": round(float(np.percentile(lat, 50)), 1),
        "latency_ms_p95": round(float(np.percentile(lat, 95)), 1),
        "throughput_fps": round(1000.0 / float(lat.mean()), 1) if lat.mean() else 0,
    }
    if base_mem is not None and peak_mem is not None and segmenter.device == "cuda":
        result["gpu_mem_used_mb"] = round(peak_mem, 0)
        result["gpu_mem_delta_mb"] = round(peak_mem - base_mem, 0)
    else:
        result["gpu_mem"] = ("not measured (CPU backend, or no CUDA provider / GPU)")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="all",
                        choices=["all", "grabcut", "sam_onnx"])
    parser.add_argument("--boxes", type=int, default=50)
    args = parser.parse_args()

    settings = get_settings()
    img, box = make_scene()
    print(f"Benchmarking on a {img.shape[1]}x{img.shape[0]} frame, "
          f"{args.boxes} segmentations per backend.\n")

    rows = []
    if args.backend in ("all", "grabcut"):
        from prahari.edge.segment.grabcut import GrabCutSegmenter
        rows.append(bench_backend("grabcut", GrabCutSegmenter(), img, box, args.boxes))

    if args.backend in ("all", "sam_onnx"):
        try:
            from prahari.edge.segment.sam_onnx import SamOnnxSegmenter
            seg = SamOnnxSegmenter(settings.sam_encoder_path,
                                   settings.sam_decoder_path,
                                   device=settings.device)
            rows.append(bench_backend("sam_onnx", seg, img, box, args.boxes))
        except Exception as exc:
            rows.append({"backend": "sam2-onnx", "status": f"unavailable: {exc}"})

    print(f"{'backend':<26} {'device':<6} {'mean ms':<9} {'p95 ms':<8} "
          f"{'fps':<6} {'ok':<7} gpu")
    for r in rows:
        if r.get("status"):
            print(f"{r['backend']:<26} {r['status']}")
            continue
        gpu = (f"{r.get('gpu_mem_delta_mb')} MB" if 'gpu_mem_delta_mb' in r
               else "n/a (CPU)")
        print(f"{r['backend']:<26} {r['device']:<6} "
              f"{r['latency_ms_mean']:<9} {r['latency_ms_p95']:<8} "
              f"{r['throughput_fps']:<6} {r['succeeded']}/{r['runs']:<5} {gpu}")

    mem = gpu_memory_mb()
    print(f"\nGPU: {'present, ' + str(int(mem)) + ' MB in use' if mem is not None else 'nvidia-smi not available'}")
    print("Note: SAM GPU numbers require onnxruntime-gpu + the SAM ONNX weights, "
          "neither of which could be installed on the build machine's connection. "
          "Run this on the target GPU box to size the SAM configuration.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
