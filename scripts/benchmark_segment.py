"""Benchmark the segmentation backends: latency, throughput, and GPU memory.

    python scripts/benchmark_segment.py
    python scripts/benchmark_segment.py --backend grabcut --boxes 100
    python scripts/benchmark_segment.py --mot datasets/MOT17/train/MOT17-04-FRCNN

Reports what it can honestly measure on this machine. Latency and throughput are
always real. GPU memory is reported only when a CUDA execution provider and an
NVIDIA GPU are actually present - on a CPU-only install it says so rather than
inventing a figure, because "VRAM used by a backend that ran on the CPU" is not a
number that means anything.

The point of this benchmark for an RTX 4050 (6 GB) is to answer, before wiring
SAM into a live node, whether the chosen configuration fits in memory and runs
fast enough to be invoked on an event without stalling the pipeline.

Two things this reports that a naive benchmark does not:

**Contention.** A latency figure taken while another process has the GPU at 100%
measures the queue, not the model. The run checks for other CUDA compute
processes and GPU utilisation before starting, prints what it found, and marks
the affected rows - because an unqualified "SAM 2 takes 1.1 s on a 4050" would be
a fabricated characteristic of the model.

**Mask quality.** Latency alone cannot choose between the backends: GrabCut is
the faster of the two and that is not the question. The scene has a known
foreground, so each backend's mask is scored as IoU against it. That is what
makes "SAM is worth the GPU" a measurement rather than an assumption.

The synthetic scene is a flat-coloured foreground on a flat background, which is
exactly the case GrabCut's colour model solves perfectly - the right test for
whether a backend *works*, and the wrong one for ranking two that both do. Pass
`--mot <sequence>` to run the same comparison on real frames, prompted with the
sequence's ground-truth boxes. There is no mask ground truth there, so that mode
reports latency on real imagery and the *agreement* between the two backends
rather than an accuracy score: where they disagree is where the approximate one
is bleeding into background, and it is measured instead of asserted.
"""
from __future__ import annotations

import argparse
import os
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


def gpu_contention() -> dict:
    """Other CUDA work on this GPU, which would make any timing meaningless."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return {"known": False}
    info: dict = {"known": True, "other_processes": [], "utilisation_percent": None}
    try:
        util = subprocess.check_output(
            [exe, "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
            text=True, timeout=10)
        info["utilisation_percent"] = int(util.strip().splitlines()[0])
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            [exe, "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
            text=True, timeout=10)
        mine = str(os.getpid())
        for line in out.strip().splitlines():
            if not line.strip():
                continue
            pid = line.split(",")[0].strip()
            if pid != mine:
                info["other_processes"].append(line.strip())
    except Exception:
        pass
    info["busy"] = bool(info["other_processes"]) or (
        (info["utilisation_percent"] or 0) >= 50)
    return info


def load_real_frames(sequence: Path, limit: int):
    """Real frames from a MOT sequence, each with a ground-truth box prompt.

    Boxes come from the sequence annotations, so the prompts are where people
    actually are rather than where a detector guessed - the segmenter is being
    measured here, not the detector in front of it.
    """
    import cv2

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from prahari.eval.mot import MotSequenceSource

    source = MotSequenceSource(sequence)
    if not source.open():
        raise RuntimeError(f"could not open MOT sequence {sequence}")

    samples = []
    try:
        while len(samples) < limit:
            frame = source.read()
            if frame is None:
                break
            boxes = [e for e in (frame.ground_truth or [])
                     if e.get("object_class") == "person"]
            if not boxes:
                continue
            # The largest subject in the frame: a 12-pixel-tall pedestrian at
            # the back of a crowd measures nothing about either backend.
            entry = max(boxes, key=lambda e: ((e["bbox"][2] - e["bbox"][0]) *
                                              (e["bbox"][3] - e["bbox"][1])))
            x1, y1, x2, y2 = entry["bbox"]
            if (x2 - x1) < 24 or (y2 - y1) < 48:
                continue
            samples.append((frame.image.copy(),
                            BBox(x1=x1, y1=y1, x2=x2, y2=y2)))
    finally:
        source.close()
    return samples


def bench_real(name: str, segmenter, samples) -> dict:
    """Latency on real imagery, plus each mask for cross-backend agreement."""
    if segmenter is None:
        return {"backend": name, "status": "not available"}, []

    segmenter.warmup()
    base_mem = gpu_memory_mb()
    latencies, masks = [], []
    ok = 0
    for img, box in samples:
        t0 = time.perf_counter()
        r = segmenter.segment(img, box)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        masks.append((r, img.shape[:2]))
        if r is not None:
            ok += 1
    peak_mem = gpu_memory_mb()

    lat = np.array(latencies) if latencies else np.array([0.0])
    result = {
        "backend": segmenter.describe().get("name", name),
        "device": segmenter.describe().get("device", "cpu"),
        "runs": len(samples),
        "succeeded": ok,
        "latency_ms_mean": round(float(lat.mean()), 1),
        "latency_ms_p95": round(float(np.percentile(lat, 95)), 1),
        "throughput_fps": round(1000.0 / float(lat.mean()), 1) if lat.mean() else 0,
    }
    if base_mem is not None and peak_mem is not None and segmenter.device == "cuda":
        result["gpu_mem_used_mb"] = round(peak_mem, 0)
        result["gpu_mem_delta_mb"] = round(peak_mem - base_mem, 0)
    return result, masks


def _to_full(result, shape):
    if result is None or getattr(result, "mask", None) is None:
        return None
    full = np.zeros(shape, np.uint8)
    mask = np.asarray(result.mask)
    ox, oy = result.offset
    oy, ox = int(oy), int(ox)
    h = min(mask.shape[0], shape[0] - oy)
    w = min(mask.shape[1], shape[1] - ox)
    if h <= 0 or w <= 0:
        return None
    full[oy:oy + h, ox:ox + w] = (mask[:h, :w] > 0).astype(np.uint8)
    return full


def agreement(masks_a, masks_b) -> float | None:
    """Mean IoU between two backends' masks on the same prompts."""
    scores = []
    for (ra, shape), (rb, _) in zip(masks_a, masks_b):
        fa, fb = _to_full(ra, shape), _to_full(rb, shape)
        if fa is None or fb is None:
            continue
        union = int(np.logical_or(fa, fb).sum())
        if union:
            scores.append(int(np.logical_and(fa, fb).sum()) / union)
    return round(float(np.mean(scores)), 3) if scores else None


def make_scene(seed: int = 7):
    """A frame with a clear foreground object, a box, and the true mask."""
    import cv2

    rng = np.random.default_rng(seed)
    img = np.full((480, 640, 3), (60, 90, 70), np.uint8)
    img = (img + rng.normal(0, 6, img.shape)).clip(0, 255).astype(np.uint8)
    cv2.rectangle(img, (280, 150), (350, 380), (200, 180, 160), -1)
    cv2.circle(img, (315, 135), 22, (200, 180, 160), -1)
    box = BBox(x1=278, y1=112, x2=352, y2=378)

    truth = np.zeros(img.shape[:2], np.uint8)
    cv2.rectangle(truth, (280, 150), (350, 380), 1, -1)
    cv2.circle(truth, (315, 135), 22, 1, -1)
    return img, box, truth


def mask_iou(result, truth: np.ndarray) -> float | None:
    """IoU of a SegmentResult's bbox-local mask against the full-frame truth."""
    if result is None or getattr(result, "mask", None) is None:
        return None
    full = np.zeros(truth.shape, np.uint8)
    mask = np.asarray(result.mask)
    ox, oy = getattr(result, "offset", (0, 0))
    h, w = mask.shape[:2]
    oy, ox = int(oy), int(ox)
    h = min(h, truth.shape[0] - oy)
    w = min(w, truth.shape[1] - ox)
    if h <= 0 or w <= 0:
        return None
    full[oy:oy + h, ox:ox + w] = (mask[:h, :w] > 0).astype(np.uint8)
    inter = int(np.logical_and(full, truth).sum())
    union = int(np.logical_or(full, truth).sum())
    return round(inter / union, 3) if union else None


def bench_backend(name: str, segmenter, img, box, n: int, truth=None) -> dict:
    if segmenter is None:
        return {"backend": name, "status": "not available"}

    segmenter.warmup()
    base_mem = gpu_memory_mb()
    latencies = []
    ious = []
    ok = 0
    for _ in range(n):
        t0 = time.perf_counter()
        r = segmenter.segment(img, box)
        latencies.append((time.perf_counter() - t0) * 1000.0)
        if r is not None:
            ok += 1
            if truth is not None:
                iou = mask_iou(r, truth)
                if iou is not None:
                    ious.append(iou)
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
        "mask_iou_mean": round(float(np.mean(ious)), 3) if ious else None,
    }
    if base_mem is not None and peak_mem is not None and segmenter.device == "cuda":
        result["gpu_mem_used_mb"] = round(peak_mem, 0)
        result["gpu_mem_delta_mb"] = round(peak_mem - base_mem, 0)
    else:
        result["gpu_mem"] = ("not measured (CPU backend, or no CUDA provider / GPU)")
    return result


def run_real(args) -> int:
    """Benchmark both backends on real frames with ground-truth box prompts."""
    settings = get_settings()
    samples = load_real_frames(args.mot, args.boxes)
    if not samples:
        print(f"No usable ground-truth subjects found in {args.mot}.")
        return 1
    h, w = samples[0][0].shape[:2]
    print(f"Benchmarking on {len(samples)} real frames from {args.mot.name} "
          f"({w}x{h}), prompted with ground-truth boxes.")

    contention = gpu_contention()
    if contention.get("known") and contention.get("busy"):
        print()
        print("  !! ANOTHER PROCESS IS USING THIS GPU - CUDA timings below "
              "measure contention,")
        print("     not the model. Re-run on an idle GPU for a figure worth "
              "quoting.")
        for proc in contention["other_processes"]:
            print(f"     {proc}")
    print()

    from prahari.edge.segment.grabcut import GrabCutSegmenter

    rows, masks = [], {}
    row, m = bench_real("grabcut", GrabCutSegmenter(), samples)
    rows.append(row)
    masks["grabcut"] = m

    try:
        from prahari.edge.segment.sam_onnx import SamOnnxSegmenter

        seg = SamOnnxSegmenter(settings.sam_encoder_path,
                               settings.sam_decoder_path,
                               device=settings.device,
                               cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))
        row, m = bench_real("sam_onnx", seg, samples)
        rows.append(row)
        masks["sam"] = m
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

    if "sam" in masks:
        overlap = agreement(masks["grabcut"], masks["sam"])
        print()
        if overlap is None:
            print("Backend agreement: not computable (one backend produced no masks).")
        else:
            print(f"Backend agreement (mean IoU between the two masks on the same "
                  f"prompt): {overlap:.3f}")
            print("There is no mask ground truth in MOT, so this is not an accuracy")
            print("score for either backend. It says how far apart they are: a low")
            print("figure on cluttered frames is where the approximate backend is")
            print("bleeding into background, and is the case that justifies the GPU.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="all",
                        choices=["all", "grabcut", "sam_onnx"])
    parser.add_argument("--boxes", type=int, default=50)
    parser.add_argument("--mot", type=Path, default=None,
                        help="a MOT sequence directory; benchmark on its real "
                             "frames, prompted with its ground-truth boxes")
    args = parser.parse_args()

    if args.mot is not None:
        return run_real(args)

    settings = get_settings()
    img, box, truth = make_scene()
    print(f"Benchmarking on a {img.shape[1]}x{img.shape[0]} frame, "
          f"{args.boxes} segmentations per backend.")

    contention = gpu_contention()
    if contention.get("known") and contention.get("busy"):
        print()
        print("  !! ANOTHER PROCESS IS USING THIS GPU.")
        if contention["utilisation_percent"] is not None:
            print(f"     GPU utilisation before starting: "
                  f"{contention['utilisation_percent']}%")
        for proc in contention["other_processes"]:
            print(f"     {proc}")
        print("     Any CUDA timing below measures contention for the device, not")
        print("     the model. Treat the GPU latency as an upper bound only, and")
        print("     re-run when the GPU is idle for a figure worth quoting.")
    print()

    rows = []
    if args.backend in ("all", "grabcut"):
        from prahari.edge.segment.grabcut import GrabCutSegmenter
        rows.append(bench_backend("grabcut", GrabCutSegmenter(), img, box,
                                  args.boxes, truth))

    if args.backend in ("all", "sam_onnx"):
        try:
            from prahari.edge.segment.sam_onnx import SamOnnxSegmenter
            seg = SamOnnxSegmenter(settings.sam_encoder_path,
                                   settings.sam_decoder_path,
                                   device=settings.device,
                                   cuda_dll_dir=getattr(settings, "cuda_dll_dir",
                                                        None))
            rows.append(bench_backend("sam_onnx", seg, img, box, args.boxes, truth))
        except Exception as exc:
            rows.append({"backend": "sam2-onnx", "status": f"unavailable: {exc}"})

    print(f"{'backend':<26} {'device':<6} {'mean ms':<9} {'p95 ms':<8} "
          f"{'fps':<6} {'mask IoU':<9} {'ok':<7} gpu")
    for r in rows:
        if r.get("status"):
            print(f"{r['backend']:<26} {r['status']}")
            continue
        gpu = (f"{r.get('gpu_mem_delta_mb')} MB" if 'gpu_mem_delta_mb' in r
               else "n/a (CPU)")
        iou = r.get("mask_iou_mean")
        print(f"{r['backend']:<26} {r['device']:<6} "
              f"{r['latency_ms_mean']:<9} {r['latency_ms_p95']:<8} "
              f"{r['throughput_fps']:<6} "
              f"{(f'{iou:.3f}' if iou is not None else '-'):<9} "
              f"{r['succeeded']}/{r['runs']:<5} {gpu}")

    mem = gpu_memory_mb()
    print(f"\nGPU: {'present, ' + str(int(mem)) + ' MB in use' if mem is not None else 'nvidia-smi not available'}")
    if contention.get("busy"):
        print("Timings above were taken with another process on the GPU - see the "
              "warning at the top. The mask-IoU column is unaffected by "
              "contention; only the latency figures are.")
    print("Mask IoU is scored against the synthetic scene's known foreground. It "
          "compares the backends on the same shape; it is not a claim about "
          "accuracy on real border imagery.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
