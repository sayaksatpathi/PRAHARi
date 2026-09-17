"""Controlled detector benchmark on MOT17.

One variable at a time. The evaluation harness, the sequences, the tracker, the
matching thresholds and the scoring are identical across every row; only the
detector model, its input resolution and the execution provider change. Anything
else would make the comparison a story rather than a measurement.

    python scripts/benchmark_detectors.py
    python scripts/benchmark_detectors.py --device cuda --only C

The three CPU configurations separate two effects that are easy to confuse:

    A  YOLOX-Tiny @ 416   the released export, and the current baseline
    B  YOLOX-Tiny @ 640   same weights, larger input  -> isolates resolution
    C  YOLOX-S    @ 640   larger model, same input    -> isolates capacity

YOLOX publishes Tiny at 416 and S at 640, so A and C are the models at their
intended operating points and B is the controlled middle term. Without B, a gain
from A to C cannot be attributed: MOT17 is 1920x1080, where a 60 px pedestrian
becomes ~13 px at a 416 input, so resolution alone is a plausible explanation for
most of the difference.

VRAM is sampled from nvidia-smi around the run and reported only for CUDA rows.
On CPU it is not applicable, and reporting a number there would be noise.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

CONFIGS = [
    ("A", "YOLOX-Tiny @416", "yolox_tiny.onnx", 0),
    ("B", "YOLOX-Tiny @640", "yolox_tiny_dyn.onnx", 640),
    ("C", "YOLOX-S @640", "yolox_s.onnx", 0),
    ("D", "YOLOv8n Fine-Tuned", "yolov8n_ft.onnx", 640),
]


def gpu_sample() -> tuple[float | None, list[str]]:
    """(used MB, other compute processes). Contention invalidates timings."""
    exe = shutil.which("nvidia-smi")
    if not exe:
        return None, []
    used = None
    others: list[str] = []
    try:
        used = float(subprocess.check_output(
            [exe, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True, timeout=10).strip().splitlines()[0])
        mine = str(os.getpid())
        out = subprocess.check_output(
            [exe, "--query-compute-apps=pid,process_name", "--format=csv,noheader"],
            text=True, timeout=10)
        for line in out.strip().splitlines():
            if line.strip() and line.split(",")[0].strip() != mine:
                others.append(line.strip())
    except Exception:
        pass
    return used, others


def run_one(key: str, label: str, model: str, input_size: int, device: str,
            mot: Path, sequences: int, out_root: Path) -> dict:
    out_dir = out_root / f"{key}_{device}"
    env = dict(os.environ)
    env.update({
        "PRAHARI_DEVICE": device,
        "PRAHARI_MODEL_PATH": f"./models/{model}",
        "PRAHARI_DETECTOR_INPUT_SIZE": str(input_size),
    })

    print(f"\n=== {key}. {label} on {device.upper()} ===", flush=True)
    base_mem, others = gpu_sample()
    if others and device == "cuda":
        print("  !! another process holds this GPU - timings measure contention:")
        for o in others:
            print(f"     {o}")

    t0 = time.perf_counter()
    proc = subprocess.run(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"),
         str(ROOT / "scripts" / "evaluate.py"),
         "--mot", str(mot), "--max-sequences", str(sequences),
         "--out", str(out_dir)],
        cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=14400)
    wall = time.perf_counter() - t0
    peak_mem, _ = gpu_sample()

    if proc.returncode != 0:
        print(f"  FAILED (exit {proc.returncode})")
        print("  " + (proc.stdout or proc.stderr)[-600:].replace("\n", "\n  "))
        return {"key": key, "label": label, "device": device, "failed": True}

    report = json.loads((out_dir / "evaluation.json").read_text())
    tp = fp = fn = idsw = mt = ml = 0
    matches = gt = pred = 0
    motp, idf1, fps, ms = [], [], [], []
    for r in report["scenarios"]:
        d, t, p = r["detection"], r["tracking"], r["performance"]
        tp += d["true_positive"]; fp += d["false_positive"]; fn += d["false_negative"]
        gt += t["gt_detections"]; pred += t["predicted_detections"]
        matches += t["matches"]; idsw += t["id_switches"]
        mt += t["mostly_tracked"]; ml += t["mostly_lost"]
        motp.append(t["motp_iou"]); idf1.append(t["idf1"])
        fps.append(p["throughput_fps"]); ms.append(p["mean_ms_per_frame"])

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    # Pooled CLEAR-MOT over both sequences, from the raw counts rather than an
    # average of per-sequence MOTA - averaging a ratio across sequences of very
    # different length weights the short one far too heavily.
    mota = 1 - ((gt - matches) + (pred - matches) + idsw) / gt if gt else 0.0

    row = {
        "key": key, "label": label, "device": device, "model": model,
        "input_size": input_size or "from model",
        "precision": round(precision, 3), "recall": round(recall, 3),
        "f1": round(2 * precision * recall / (precision + recall), 3)
              if precision + recall else 0.0,
        "mota": round(mota, 3),
        "motp": round(statistics.mean(motp), 3),
        "idf1": round(statistics.mean(idf1), 3),
        "id_switches": idsw, "mostly_tracked": mt, "mostly_lost": ml,
        "fps": round(statistics.mean(fps), 1),
        "latency_ms": round(statistics.mean(ms), 1),
        "wall_seconds": round(wall, 1),
        "tp": tp, "fp": fp, "fn": fn,
        "gpu_mem_mb": (round(peak_mem) if device == "cuda" and peak_mem else None),
        "gpu_mem_delta_mb": (round(peak_mem - base_mem)
                             if device == "cuda" and peak_mem and base_mem else None),
        "gpu_contended": bool(others) if device == "cuda" else False,
    }
    print(f"  P={row['precision']:.3f} R={row['recall']:.3f} MOTA={row['mota']:.3f} "
          f"IDF1={row['idf1']:.3f} IDsw={idsw} {row['fps']:.1f} fps "
          f"{row['latency_ms']:.1f} ms")
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--mot", type=Path, default=Path("datasets/MOT17/train"))
    parser.add_argument("--max-sequences", type=int, default=2)
    parser.add_argument("--device", default="cpu", choices=["cpu", "cuda", "both"])
    parser.add_argument("--only", default="", help="comma-separated keys, e.g. B,C")
    parser.add_argument("--out", type=Path, default=Path("var/bench_detectors"))
    args = parser.parse_args()

    wanted = {k.strip().upper() for k in args.only.split(",") if k.strip()}
    configs = [c for c in CONFIGS if not wanted or c[0] in wanted]
    devices = ["cpu", "cuda"] if args.device == "both" else [args.device]

    missing = [m for _, _, m, _ in configs if not (ROOT / "models" / m).exists()]
    if missing:
        print(f"Missing model file(s): {', '.join(missing)}")
        return 1

    rows = []
    for device in devices:
        for key, label, model, size in configs:
            rows.append(run_one(key, label, model, size, device,
                                args.mot, args.max_sequences, args.out))

    rows = [r for r in rows if not r.get("failed")]
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "summary.json").write_text(json.dumps(rows, indent=2))

    print("\n" + "=" * 104)
    print(f"{'':2} {'configuration':<18}{'dev':<6}{'P':>7}{'R':>7}{'F1':>7}"
          f"{'MOTA':>8}{'MOTP':>7}{'IDF1':>7}{'IDsw':>6}{'MT':>4}{'ML':>4}"
          f"{'fps':>7}{'ms':>7}{'VRAM':>8}")
    print("-" * 104)
    for r in rows:
        vram = (f"{r['gpu_mem_delta_mb']}MB" if r.get("gpu_mem_delta_mb") is not None
                else ("n/a" if r["device"] == "cpu" else "-"))
        print(f"{r['key']:<2} {r['label']:<18}{r['device']:<6}"
              f"{r['precision']:>7.3f}{r['recall']:>7.3f}{r['f1']:>7.3f}"
              f"{r['mota']:>8.3f}{r['motp']:>7.3f}{r['idf1']:>7.3f}"
              f"{r['id_switches']:>6}{r['mostly_tracked']:>4}{r['mostly_lost']:>4}"
              f"{r['fps']:>7.1f}{r['latency_ms']:>7.1f}{vram:>8}")
    print("=" * 104)
    if any(r.get("gpu_contended") for r in rows):
        print("NOTE: at least one CUDA row ran with another process on the GPU. Its")
        print("      timing and VRAM figures measure contention, not the model.")
    print(f"\nWritten: {args.out / 'summary.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
