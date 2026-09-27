# Prahari — Deployment Cost Model & Scaling Economics

> **Planning estimates, not procurement quotes.** Figures are order-of-magnitude
> for a jury/feasibility discussion, in INR, at 2026 street prices. They must be
> replaced by real vendor quotes and a real-time GPU validation (see caveats)
> before any deployment decision. Nothing here is a measured field cost.

The central cost argument: **Prahari adds intelligence to CCTV that already
exists, so the camera capital cost is ₹0.** The spend is edge compute, storage,
and integration — not a rip-and-replace.

## 1. Unit of deployment: the edge site

One **edge node** serves a cluster of existing cameras at a Border Out-Post (BOP)
or checkpost. Sizing comes from the measured per-stream cost.

**Measured basis (CPU):** 12.6 fps per stream on CPU (MOT17).

**Measured basis (GPU, production ONNX-CUDA path).** The cameras-per-node
multiplier is no longer a guess. Measured on an RTX 4050 Laptop GPU (6 GB) with
the *production* runtime — ONNX Runtime `CUDAExecutionProvider`, single stream,
batch 1 — via `scripts/benchmark_gpu_detectors.py`:

| Detector | Input | Throughput | VRAM |
|----------|-------|-----------|------|
| YOLOX-Tiny | 416 | **~190 fps** | small |
| YOLOX-S (production) | 640 | **~104 fps** | ~0.15 GB |
| YOLOv8s (MOT-tuned) | 640 | **~124 fps** | ~0.15 GB |
| YOLOv8m (accuracy config) | 1280 | **~13 fps** | ~1.0 GB |

Deriving cameras-per-node from the production detector (YOLOX-S, ~104 fps): with
`PRAHARI_INFERENCE_INTERVAL=2` on a 12–15 fps sub-stream, each camera needs
~6–8 detections/s, so the detector alone serves ~13 streams; derating ~40% for
the rest of the pipeline (per-frame tracking, rules, evidence, decode) gives a
grounded planning figure of **~5–8 cameras per edge node** — the upper half of
the previous conservative 4–6, on a *laptop* GPU. A Jetson Orin NX 16 GB or an
edge mini-PC with an entry discrete GPU should meet or exceed this.

*Honest caveat unchanged in kind:* this is measured on an RTX 4050 as a proxy,
not on the target edge accelerator, and it is pure detector throughput (an upper
bound). Re-measure on the chosen accelerator with the full pipeline before any
procurement decision. What has changed is that the multiplier now rests on a
reproducible measurement of the production runtime, not an expectation.

## 2. Per-site edge BOM (serves ~4–6 existing cameras)

| Item | Spec | Est. unit ₹ | Notes |
|------|------|-------------|-------|
| Existing cameras | reused | **0** | the core cost saving |
| Edge compute | NVIDIA Jetson Orin NX 16 GB (or mini-PC + entry GPU) | 55,000 – 90,000 | runs the ONNX pipeline at the edge |
| Evidence storage | 1–2 TB industrial SSD | 8,000 – 15,000 | local evidence + offline queue |
| Enclosure + PoE switch | ruggedised, fanless, PoE for cameras | 12,000 – 20,000 | border environment |
| UPS / solar buffer | for grid-unreliable BOPs | 10,000 – 25,000 | remote-site resilience |
| Networking (uplink) | existing VSAT/4G/radio | 0 – 5,000 | reuse existing link where present |
| Integration / install | labour, config, capability profiling | 10,000 – 20,000 | one-time |
| **Per-site total (one-time)** | | **≈ 1.0 – 1.8 lakh** | for 4–6 cameras |

**Software:** the Prahari stack is open (models with clear licenses — YuNet
Apache/MIT for face, YOLOX Apache for detection). **₹0 per-seat license.** The one
licensing item to resolve is any research-only model weight (tracked in
[model-provenance.md](model-provenance.md)); the deployable path uses
permissively-licensed models only.

## 2b. Power & connectivity envelope — one remote BOP

Concrete numbers for a single off-grid Border Out Post running the edge node on
4–6 existing cameras. These size the solar/UPS line above and answer "what does it
actually draw and send?".

| Quantity | Figure | Basis |
|---|---|---|
| Edge compute (idle→load) | **10–25 W** (Jetson Orin NX/Nano class) | edge-AI SoC, GPU inference at ≤25 W; a mini-PC+GPU is 45–90 W if a discrete GPU is used |
| Existing cameras | **~5–12 W each** (PoE), reused | already installed and powered — not new draw |
| Edge node energy/day | **≈ 0.25–0.6 kWh/day** | 10–25 W × 24 h |
| Battery for 48 h autonomy | **~1.2–2.4 kWh** (e.g. 100–200 Ah @ 12 V) | 2× daily energy, for two overcast days |
| Solar to recharge | **~200–400 W panel** | recharges the day's draw plus buffer in ~4–5 peak-sun hours |
| **Uplink bandwidth needed** | **≈ 30–60 kbit/s average** | **measured on the running node: ~33 kbit/s, 99.79 % less than continuous streaming**; ~1 MB per sync batch |
| Fits which links | **VSAT, 4G/LTE, even a data radio** | events + hashed evidence go up metered; full video stays local |

**Why the uplink is tiny.** Prahari never streams video to the core. It processes
on the edge and ships only **events + SHA-256-hashed evidence**, queued through an
outage and synced on reconnect. The dashboard's own "Uplink economics" panel shows
the live figure — **~33 kbit/s and a 99.79 % reduction against continuous
streaming** — which is what makes a solar-powered VSAT BOP viable at all.

**Honest scope:** these are engineering estimates from the measured bandwidth and
standard edge-AI/solar sizing, not a site survey. Real mounting, grid quality and
link availability at a specific BOP still need a survey (see §7).

## 3. Sector core (aggregation)

| Item | Spec | Est. ₹ |
|------|------|--------|
| Sector server | 1 GPU server aggregating ~20–50 sites | 4 – 8 lakh |
| Dashboard/storage | NVR-class storage + the zero-build web UI | included |

One sector core per ~20–50 BOPs.

## 4. Scaling math — 500 cameras

Assume **500 existing cameras**, 5 cameras/edge node → **100 edge nodes**, across
**~4 sector cores** (25 nodes each).

| Line | Calc | Est. ₹ |
|------|------|--------|
| Edge nodes | 100 × ~1.4 lakh | ≈ 1.4 crore |
| Sector cores | 4 × ~6 lakh | ≈ 24 lakh |
| Cameras | 500 × 0 (reused) | 0 |
| **One-time capex (500 cams)** | | **≈ 1.6 crore** |
| Per-camera one-time | 1.6 cr / 500 | **≈ 32,000 / camera** |

**Opex (annual, planning):** power + connectivity + maintenance + model-update
labour ≈ **10–15% of capex/yr** → ≈ 16–24 lakh/yr for 500 cameras. Add a spares
pool (~5% of edge nodes).

## 5. Cost drivers & sensitivities

- **Cameras/node ratio** is the dominant lever, and it is now measured rather
  than assumed (§1): the production detector sustains ~104 fps on a laptop GPU,
  supporting ~5–8 cameras/node. At the 8 end, edge capex drops ~35% versus the
  old conservative 5. Confirming this on the target accelerator with the full
  pipeline is the remaining highest-value cost measurement.
- **Storage retention policy** drives SSD size (evidence hash-chain + clips).
- **Remote power** (solar/UPS) dominates for off-grid BOPs.
- **No per-camera license or cloud fee** — the recurring cost is power, link, and
  maintenance, not software rent. This is a structural advantage over commercial
  VMS-per-channel licensing.

## 6. Cost vs. the alternative

- **Rip-and-replace with new smart cameras:** ₹15,000–40,000 *per camera* in
  hardware alone, plus install — i.e. **₹75 lakh–2 crore just in cameras** for 500
  sites, before analytics. Prahari's reuse-existing-CCTV model avoids that entirely.
- **Commercial VMS analytics (per-channel license):** recurring per-camera fees;
  Prahari's open, licensable-model stack removes the per-channel rent.

## 7. Honest caveats (do not hide these)

- **Every ₹ figure is a planning estimate, not a quote.** Real BOM needs vendor
  pricing and MHA/SSB procurement rules.
- **Cameras-per-node** is now grounded in a measured single-stream GPU benchmark
  (~104 fps, YOLOX-S, ORT-CUDA on an RTX 4050 — §1), but a real *multi-stream*
  test on the *target* accelerator with the full pipeline is still owed. The
  per-camera cost hinges on this final confirmation.
- **No real site survey** — power, connectivity, and mounting realities at actual
  BOPs will move these numbers.
- Costs assume the existing CCTV is serviceable; cameras too degraded to certify
  (Prahari measures this) may still need replacement, which this model excludes.
