#!/usr/bin/env bash
# Waits for a genuinely idle GPU, then runs the two outstanding measurements:
#   1. CUDA detector benchmark, B and C only (A@416 is settled on CPU)
#   2. uncontended SAM 2 vs GrabCut on real MOT17 frames
# Both are timing measurements, so a contended device makes them meaningless -
# hence the wait rather than a run-now-and-caveat.
cd "C:/Users/Sayak Satpathi/OneDrive/Desktop/webdev/prahari" || exit 1

echo "waiting for an idle GPU..."
IDLE=0
while true; do
  OTHERS=$(nvidia-smi --query-compute-apps=pid --format=csv,noheader | grep -c . || true)
  UTIL=$(nvidia-smi --query-gpu=utilization.gpu --format=csv,noheader,nounits)
  if [ "$OTHERS" -eq 0 ] && [ "$UTIL" -lt 20 ]; then
    IDLE=$((IDLE+1))
    # Require three consecutive quiet samples: a training run between epochs
    # can read as idle for a moment and then take the device back mid-benchmark.
    if [ "$IDLE" -ge 3 ]; then
      echo "GPU idle (util ${UTIL}%, no compute processes) - starting benchmarks"
      break
    fi
  else
    IDLE=0
  fi
  sleep 60
done

nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader > gpu_baseline.txt
./.venv/Scripts/python.exe scripts/benchmark_detectors.py --device cuda --only B,C > bench_det_cuda.log 2>&1
echo "CUDA_DETECTOR_DONE exit=$?"
./.venv/Scripts/python.exe scripts/benchmark_segment.py --mot datasets/MOT17/train/MOT17-02-FRCNN --boxes 30 > bench_sam_clean.log 2>&1
echo "SAM_CLEAN_REAL_DONE exit=$?"
./.venv/Scripts/python.exe scripts/benchmark_segment.py --boxes 50 > bench_sam_synth.log 2>&1
echo "SAM_CLEAN_SYNTH_DONE exit=$?"
echo "ALL_GPU_BENCHMARKS_DONE"
