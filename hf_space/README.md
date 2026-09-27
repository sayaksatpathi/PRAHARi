---
title: Prahari Border Video Intelligence
emoji: 🛡️
colorFrom: blue
colorTo: gray
sdk: streamlit
sdk_version: 1.39.0
app_file: app.py
pinned: false
license: mit
---

# Prahari — Border Video Intelligence (showcase)

An AI video-intelligence layer for **existing** border CCTV. Built for
**SIH26187** (Ministry of Home Affairs / Sashastra Seema Bal), **Blockchain &
Cybersecurity** track.

This Space is a **free, always-on showcase**:

- The **live-camera clips** are the real pipeline's output (detection, tracking,
  zones, ANPR, thermal/night styling), pre-rendered so they replay on CPU with no
  GPU or model needed.
- The **Evidence Integrity** demo runs **live in pure Python** — it builds a
  SHA-256 hash chain, core-countersigns it, and catches a full-chain rewrite that
  the edge check alone would pass. This mirrors the deployed
  `prahari/core/notary.py` exactly.

The full real-time GPU pipeline (6-camera detection, Awiros Indian ANPR, SAM2
segmentation, tamper detection, the two-tier countersigned ledger, the operator
dashboard) runs on an edge node with a GPU.

**Full source, tests and threat model:**
https://github.com/sayaksatpathi/PRAHARi
