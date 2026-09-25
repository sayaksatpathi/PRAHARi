# Prahari — Bill of Materials (BOM)

> **Planning BOM, not a procurement quote.** Every figure is an order-of-magnitude
> planning estimate in INR at 2026 street prices, for a jury / feasibility
> discussion. Real procurement requires vendor quotes and MHA/SSB purchase rules.
> The economic model, sensitivities and cost-vs-alternative analysis live in
> [deployment-cost-model.md](deployment-cost-model.md); this document is the
> itemised parts list that model rests on.

The one-line argument: **the cameras already exist, so camera capital is ₹0.**
Prahari is an intelligence layer over installed CCTV, not a rip-and-replace.

---

## 1. Reference configurations

Three tiers so the BOM scales with the site, not one size for all.

| Tier | Cameras/node | Edge compute class | When |
|------|-------------|--------------------|------|
| **Small BOP** | 1–4 | Jetson Orin Nano 8 GB / mini-PC + no dGPU | low camera count, tight power |
| **Standard BOP** | 5–8 | Jetson Orin NX 16 GB / mini-PC + entry dGPU | the common case (see throughput basis §5) |
| **Checkpost / ICP** | 8–12 | mini-PC + RTX-class dGPU | ANPR-heavy, mains power |

The **cameras-per-node** figure is measured, not assumed — ~104 fps for the
production detector (YOLOX-S) on the ORT-CUDA path, deriving ~5–8 cameras/node.
See [deployment-cost-model.md §1](deployment-cost-model.md) and
`scripts/benchmark_gpu_detectors.py`.

---

## 2. Per-site edge node BOM (Standard BOP, ~5–8 existing cameras)

| # | Item | Reference spec | Qty | Unit ₹ | Line ₹ | Type |
|---|------|----------------|-----|--------|--------|------|
| 1 | Existing cameras | reused, capability-profiled in place | n | **0** | **0** | — |
| 2 | Edge compute | NVIDIA Jetson Orin NX 16 GB, **or** fanless mini-PC (Core i5/i7) + entry GPU (≥6 GB, e.g. RTX 2000-Ada/4050-class) | 1 | 55,000–90,000 | 55,000–90,000 | capex |
| 3 | Evidence storage | 1–2 TB industrial/endurance SSD (evidence hash-chain + offline queue) | 1 | 8,000–15,000 | 8,000–15,000 | capex |
| 4 | Boot / OS media | 256 GB industrial SSD or eMMC | 1 | 2,000–4,000 | 2,000–4,000 | capex |
| 5 | Enclosure | ruggedised, fanless, IP-rated, DIN/wall mount | 1 | 6,000–12,000 | 6,000–12,000 | capex |
| 6 | PoE switch | managed, PoE+ for the camera cluster, industrial temp | 1 | 6,000–10,000 | 6,000–10,000 | capex |
| 7 | UPS / power buffer | for grid-unreliable BOPs; solar buffer where off-grid | 1 | 10,000–25,000 | 10,000–25,000 | capex |
| 8 | Uplink | reuse existing VSAT / 4G / radio backhaul | 1 | 0–5,000 | 0–5,000 | capex |
| 9 | Integration / install | labour, cabling, config, capability profiling | 1 | 10,000–20,000 | 10,000–20,000 | one-time |
| | **Per-site total (one-time)** | | | | **≈ 1.0–1.8 lakh** | |

**Recurring (per site, annual):** power + connectivity + maintenance + spares
allocation ≈ **10–15 % of capex/yr** (see §6).

---

## 3. Sector core BOM (aggregates ~20–50 sites)

| # | Item | Reference spec | Qty | Unit ₹ | Line ₹ |
|---|------|----------------|-----|--------|--------|
| 1 | Sector server | 1U/2U, 1 GPU, aggregation + chain verification + dashboard | 1 | 4,00,000–8,00,000 | 4–8 lakh |
| 2 | Core storage | NVR-class RAID for retained evidence pulled from edges | incl. | — | included |
| 3 | Dashboard | zero-build web UI (served by the core) | 1 | **0** | 0 |
| 4 | UPS | server-grade | 1 | 30,000–60,000 | 0.3–0.6 lakh |

One sector core per **~20–50 BOPs**.

---

## 4. Software BOM — licence-clean, ₹0 per-seat

Every runtime component is permissively licensed, which is a **procurement
requirement**, not a nicety, for a government deployment. No AGPL, no per-channel
rent.

| Component | Role | Licence | Cost |
|-----------|------|---------|------|
| Prahari stack (edge + core) | the product | project licence (`LICENCE`) | ₹0 |
| ONNX Runtime | inference (CPU + CUDA) | MIT | ₹0 |
| YOLOX (detector) | person/vehicle detection | Apache-2.0 | ₹0 |
| YuNet (face, **default**) | face detection | MIT/Apache path | ₹0 |
| ResNet-18 Re-ID | cross-camera re-identification | permissive | ₹0 |
| fast-alpr / Awiros ANPR | plate reading (Indian plates) | MIT / Apache-2.0 | ₹0 |
| SAM 2 / GrabCut | event-triggered segmentation | Apache-2.0 / OpenCV BSD | ₹0 |
| SQLite | edge storage (WAL, FULL) | public domain | ₹0 |
| FastAPI / Uvicorn / vanilla JS | API + dashboard | MIT/BSD | ₹0 |

**Licensing item to keep resolved:** research-only model weights (e.g. SCRFD
pretrained weights) are **not** on the deployable path — the default face
detector is YuNet (permissive). Tracked in
[model-provenance.md](model-provenance.md); see also
[docs/scrfd-licensing.md](scrfd-licensing.md).

---

## 5. Throughput basis for the node sizing

Measured, single-stream, production ONNX-CUDA path, RTX 4050 Laptop GPU (6 GB),
`scripts/benchmark_gpu_detectors.py`:

| Detector | Input | Throughput | VRAM added |
|----------|-------|-----------|-----------|
| YOLOX-Tiny | 416 | ~190 fps | small |
| YOLOX-S (production) | 640 | ~104 fps | ~0.15 GB |
| YOLOv8s (MOT-tuned) | 640 | ~124 fps | ~0.15 GB |
| YOLOv8m (accuracy config) | 1280 | ~13 fps | ~1.0 GB |

At `INFERENCE_INTERVAL=2` on a 12–15 fps sub-stream (~6–8 detections/s/camera),
YOLOX-S serves ~13 streams by detector cost alone; derating ~40 % for the rest of
the pipeline yields the **5–8 cameras/node** used above. VRAM headroom on 6 GB is
comfortable except for the 1280 accuracy config, which is a checkpost-tier choice.

---

## 6. Scaling BOM

| Fleet | Edge nodes | Sector cores | Camera capital | One-time capex | Per-camera |
|-------|-----------|-------------|----------------|----------------|-----------|
| 10 cameras | 2 (×~1.4 L) | shares 1 core | 0 | ≈ 2.8 L + core share | ≈ 30–45k |
| 100 cameras | ~15–20 | 1 (×~6 L) | 0 | ≈ 27–34 L | ≈ 30–34k |
| 500 cameras | ~65–100 | ~4 (×~6 L) | 0 | ≈ 1.15–1.6 cr | ≈ 24–32k |

*(Range endpoints follow 8 vs 5 cameras/node.)* **Opex** across the fleet:
≈ 10–15 % of capex/yr + a ~5 % spares pool of edge nodes.

**Contrast:** rip-and-replace with smart cameras is ₹15,000–40,000 *per camera*
in hardware alone — ₹75 lakh–2 crore just in cameras for 500 sites, before any
analytics. Reuse is the structural saving.

---

## 7. Assumptions & honest caveats

- **Not a quote.** Every ₹ is a planning estimate; vendor pricing and SSB/MHA
  procurement rules will move them.
- **Cameras-per-node** rests on a single-stream GPU benchmark on a *proxy* laptop
  GPU. A multi-stream test on the *target* edge accelerator with the full
  pipeline is still owed before costing is final.
- **No site survey.** Real power, connectivity and mounting at BOPs will change
  storage, UPS/solar and install lines materially.
- **Serviceable-CCTV assumption.** Cameras too degraded to certify (Prahari
  measures this) may still need replacement — excluded from these figures.
- **Thermal / IR cameras**, where a sector needs true night capability, are a
  camera-capital line this BOM does not carry (it assumes reuse of existing
  optical CCTV). See [validation-matrix.md](validation-matrix.md).
