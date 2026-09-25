"""Detector inference-throughput benchmark on the production ONNX-CUDA path.

Unlike ``benchmark_detectors.py`` (which scores accuracy over MOT17 and times the
run under that load), this measures *pure single-stream detector throughput* —
warm session, batch 1, random input at the model's native input size — plus the
CUDA VRAM the session adds. That upper-bound per-stream number is what the
deployment cost model needs to reason about cameras-per-edge-node.

Why this exists: for a while ONNX Runtime's CUDA EP could not be created on the
dev box (cuDNN 9 missing on the DLL path), so the only GPU figure was via
torch/ultralytics. ``prahari.common.cuda`` now borrows the matching CUDA 12 +
cuDNN 9 runtime from an installed PyTorch, so the *production* runtime (ORT-CUDA)
runs on the GPU and can be measured directly.

Usage:
    python scripts/benchmark_gpu_detectors.py                 # default lineup, cuda
    python scripts/benchmark_gpu_detectors.py --device cpu    # cpu baseline
    python scripts/benchmark_gpu_detectors.py --iters 50

Writes var/gpu_detector_benchmark.json. Numbers are hardware-specific; re-run on
the target edge accelerator before finalising any cost figure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from prahari.common import cuda  # noqa: E402

# (key, model path, native square input size)
DEFAULT_LINEUP = [
    ("yolox_tiny",   "models/yolox_tiny.onnx",   416),
    ("yolox_s",      "models/yolox_s.onnx",      640),
    ("yolov8s_mot",  "models/yolov8s_mot.onnx",  640),
    ("yolov8m_1280", "models/yolov8m_1280.onnx", 1280),
]


def vram_used_mb() -> int | None:
    """Total GPU memory in use right now, via nvidia-smi. None if unavailable."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used",
             "--format=csv,noheader,nounits"],
            stderr=subprocess.DEVNULL,
        ).decode().strip().splitlines()
        return int(out[0])
    except Exception:
        return None


def gpu_name() -> str:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL,
        ).decode().strip().splitlines()
        return out[0]
    except Exception:
        return "unknown"


def bench_one(path: str, size: int, device: str, iters: int) -> dict:
    import onnxruntime as ort

    providers = cuda.providers_for(device)
    base = vram_used_mb() if device == "cuda" else None
    sess = ort.InferenceSession(path, providers=providers)
    inp = sess.get_inputs()[0]
    x = np.random.rand(1, 3, size, size).astype(np.float32)

    for _ in range(3):                       # warm up
        sess.run(None, {inp.name: x})
    peak = vram_used_mb() if device == "cuda" else None

    t0 = time.perf_counter()
    for _ in range(iters):
        sess.run(None, {inp.name: x})
    dt = (time.perf_counter() - t0) / iters

    return {
        "input": size,
        "ms_per_frame": round(dt * 1000, 1),
        "fps": round(1 / dt, 1),
        "provider": sess.get_providers()[0],
        "vram_delta_mb": (peak - base) if (peak and base) else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", default="cuda", choices=["cuda", "cpu"])
    ap.add_argument("--iters", type=int, default=25)
    ap.add_argument("--out", type=Path, default=Path("var/gpu_detector_benchmark.json"))
    args = ap.parse_args()

    cuda.prepare()
    import onnxruntime as ort

    rows = {}
    print(f"device={args.device}  runtime=onnxruntime {ort.__version__}  "
          f"gpu={gpu_name() if args.device == 'cuda' else 'n/a'}")
    for key, path, size in DEFAULT_LINEUP:
        if not Path(path).exists():
            print(f"{key:14s} SKIP (missing {path})")
            rows[key] = {"skipped": f"missing {path}"}
            continue
        try:
            r = bench_one(path, size, args.device, args.iters)
            rows[key] = r
            print(f"{key:14s} @{r['input']:<4} {r['ms_per_frame']:6.1f} ms  "
                  f"{r['fps']:6.1f} fps  {r['provider']}  "
                  f"VRAM+{r['vram_delta_mb']}MB")
        except Exception as exc:                       # noqa: BLE001
            print(f"{key:14s} FAILED {exc}")
            rows[key] = {"failed": str(exc)}

    args.out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "device": gpu_name() if args.device == "cuda" else "cpu",
        "runtime": f"onnxruntime {ort.__version__} "
                   f"{'CUDAExecutionProvider' if args.device == 'cuda' else 'CPUExecutionProvider'}",
        "cuda_dll_dir": cuda.describe().get("dll_dir"),
        "iters": args.iters,
        "note": ("Pure detector inference throughput, single stream, batch 1, "
                 "warm session. Full pipeline (tracking, rules, evidence) adds "
                 "overhead; treat as an upper bound for per-stream detector cost."),
        "results": rows,
    }
    args.out.write_text(json.dumps(payload, indent=2))
    print(f"\nsaved -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
