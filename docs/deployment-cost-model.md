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

**Measured basis:** 12.6 fps per stream on CPU (MOT17). On a modern edge
accelerator (Jetson Orin NX class), the same ONNX pipeline is expected to run
several streams in real time — *this multiplier is unvalidated (see #12 GPU
benchmark) and must be measured before costing is final.* Conservative planning
assumption: **1 edge node per 4–6 cameras.**

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

- **Cameras/node ratio** is the dominant lever. If a real GPU benchmark shows
  8 cameras/node instead of 5, edge capex drops ~35%. **This is why #12 (GPU
  benchmark) is the highest-value technical fix.**
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
- **Cameras-per-node is unvalidated** until the real-time GPU benchmark (#12) and
  a real multi-stream test are done. The whole per-camera cost hinges on it.
- **No real site survey** — power, connectivity, and mounting realities at actual
  BOPs will move these numbers.
- Costs assume the existing CCTV is serviceable; cameras too degraded to certify
  (Prahari measures this) may still need replacement, which this model excludes.
