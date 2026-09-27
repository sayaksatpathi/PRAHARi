"""Prahari — AI video-intelligence for border CCTV. Hugging Face Spaces showcase.

Runs free and forever on CPU: the live pipeline's real output is pre-rendered into
the clips under assets/, and the parts that need no GPU — the tamper-evident ledger
and core-countersigning demo — run live in pure Python. Built for SIH26187,
Blockchain & Cybersecurity track.
"""
from pathlib import Path

import pandas as pd
import streamlit as st

import prahari_core as core

ASSETS = Path(__file__).parent / "assets"
GITHUB = "https://github.com/sayaksatpathi/PRAHARi"

st.set_page_config(page_title="Prahari — Border Video Intelligence",
                   page_icon="🛡️", layout="wide")

st.markdown("""
<style>
  .block-container {padding-top: 2rem; max-width: 1200px;}
  .pill {display:inline-block;padding:2px 10px;border-radius:10px;font-size:0.72rem;
         background:#16324f;color:#8fc7ff;margin-right:6px;border:1px solid #244b6e;}
  .ok {background:#123522;color:#6fe0a0;border-color:#1e5c3a;}
  .bad {background:#3a1520;color:#ff8fa3;border-color:#6e2436;}
</style>
""", unsafe_allow_html=True)

st.title("🛡️ PRAHARI")
st.caption("An AI video-intelligence layer for **existing** border CCTV · "
           "SIH26187 · Ministry of Home Affairs / Sashastra Seema Bal · "
           "**Blockchain & Cybersecurity**")

tabs = st.tabs(["Overview", "Live cameras", "Evidence integrity (live)",
                "ANPR", "Benchmarks", "Security"])

# ----------------------------------------------------------------- Overview ---
with tabs[0]:
    st.subheader("Turn the cameras that are already on the border into verifiable intelligence")
    st.markdown(
        "Prahari is a software layer over **existing CCTV** — no new hardware. Its "
        "contribution is a **cryptographically verifiable chain of custody**: every "
        "event is sealed into a **two-tier, countersigned hash-ledger** (the edge "
        "node writes, the sector core notarizes with a key the node never holds), so "
        "surveillance evidence is **tamper-resistant and admissible under BSA §65B** — "
        "even after a three-day uplink outage at a remote outpost.")
    c1, c2, c3 = st.columns(3)
    c1.markdown("#### 🔗 Two-tier ledger\nEdge writes an append-only SHA-256 chain; "
                "the core countersigns each head. History the core witnessed cannot "
                "be rewritten undetected — *try it in the Evidence tab.*")
    c2.markdown("#### 🛰️ Existing CCTV\nMeasures what each degraded camera can "
                "actually do (IEC 62676-4 DORI), then grants analytics per image "
                "region. Fenced tripwires **or** open-border pattern-of-life.")
    c3.markdown("#### 🌙 Built for the real border\nRuns day, night (IR), and thermal; "
                "reads Indian number plates; survives the offline uplink. "
                "Infiltration happens after dark — so does Prahari.")
    st.info("This page is a free, always-on **showcase**. The clips are the real "
            "pipeline's output, pre-rendered; the integrity demo runs live. The full "
            "GPU pipeline (6-camera real-time detection, ANPR, segmentation) runs on "
            f"an edge node with a GPU — code at [GitHub]({GITHUB}).")

# ------------------------------------------------------------- Live cameras ---
with tabs[1]:
    st.subheader("Six cameras — real pipeline output")
    st.caption("Detections, tracks, zones, plates and the thermal/night styling are "
               "drawn by the node. These are short captures of the live system.")
    cols = st.columns(2)
    for i, (cid, name, meta, note) in enumerate(core.CAMERAS):
        clip = ASSETS / f"{cid}.mp4"
        with cols[i % 2]:
            st.markdown(f"**{cid} — {name}**")
            if clip.exists():
                st.video(str(clip))
            st.markdown(f"<span class='pill'>{meta}</span>", unsafe_allow_html=True)
            st.caption(note)
            st.write("")

# --------------------------------------------------- Evidence integrity (live) ---
with tabs[2]:
    st.subheader("Tamper-evident → tamper-resistant, live")
    st.markdown(
        "The edge hash chain alone is **tamper-evident**: a naive edit breaks a link. "
        "But an insider who edits an event **and re-stamps every following hash** "
        "produces a chain the edge's own check accepts. The sector core defeats that "
        "by **countersigning** each chain head with a key no node holds. Run it:")

    if "events" not in st.session_state:
        st.session_state.events = [
            {"cam": "CAM-052", "type": "night_movement", "score": 0.82},
            {"cam": "CAM-011", "type": "line_crossing", "score": 0.91},
            {"cam": "CAM-022", "type": "off_route_movement", "score": 0.64},
            {"cam": "CAM-031", "type": "group_movement", "score": 0.88},
            {"cam": "CAM-014", "type": "zone_intrusion", "score": 0.79},
        ]
        st.session_state.chain = core.build_chain(st.session_state.events)
        st.session_state.witnessed = None
        st.session_state.rewritten = False

    a, b, c, d = st.columns(4)
    if a.button("① Seal events", use_container_width=True):
        st.session_state.chain = core.build_chain(st.session_state.events)
        st.session_state.witnessed = None
        st.session_state.rewritten = False
    if b.button("② Core countersign", use_container_width=True):
        head = st.session_state.chain[-1]
        st.session_state.witnessed = {
            "index": head["index"], "hash": head["hash"],
            "sig": core.core_countersign(head["index"], head["hash"], ""),
        }
    if c.button("③ Simulate rewrite", use_container_width=True):
        st.session_state.chain = core.restamp_rewrite(
            st.session_state.chain, 2,
            {"cam": "CAM-011", "type": "line_crossing", "score": 0.05})  # quiet downgrade
        st.session_state.rewritten = True
    if d.button("↺ Reset", use_container_width=True):
        for k in ("events", "chain", "witnessed", "rewritten"):
            st.session_state.pop(k, None)
        st.rerun()

    chain = st.session_state.chain
    df = pd.DataFrame([{"#": c["index"], "event": f"{c['event']['cam']} · {c['event']['type']}",
                        "score": c["event"]["score"], "entry_hash": c["hash"][:18] + "…"}
                       for c in chain])
    st.dataframe(df, hide_index=True, use_container_width=True)

    valid, broken = core.edge_verify(chain)
    e1, e2 = st.columns(2)
    with e1:
        st.markdown("**Edge chain check**")
        if valid:
            st.markdown("<span class='pill ok'>CHAIN INTACT</span> every link verifies",
                        unsafe_allow_html=True)
        else:
            st.markdown(f"<span class='pill bad'>CHAIN BROKEN</span> at #{broken}",
                        unsafe_allow_html=True)
    with e2:
        st.markdown("**Core countersignature (notary)**")
        w = st.session_state.witnessed
        if not w:
            st.caption("Press ② to countersign the current head.")
        else:
            head = chain[-1]
            rewritten = st.session_state.rewritten and head["hash"] != w["hash"]
            if rewritten:
                st.markdown("<span class='pill bad'>REWRITE DETECTED</span> "
                            "the head no longer matches the witnessed hash",
                            unsafe_allow_html=True)
            else:
                st.markdown(f"<span class='pill ok'>COUNTERSIGNED</span> "
                            f"witnessed head #{w['index']}", unsafe_allow_html=True)
            st.caption(f"witnessed hash {w['hash'][:22]}…  ·  core sig {w['sig'][:22]}…")

    if st.session_state.rewritten:
        st.warning("The rewrite kept the chain **internally consistent** — the edge "
                   "check above still says INTACT. The core's countersignature, which "
                   "the tamperer cannot reproduce, is what exposes it. **That is the "
                   "whole point.**")

# --------------------------------------------------------------------- ANPR ---
with tabs[3]:
    st.subheader("Indian number-plate recognition")
    st.markdown(
        "The gate camera is certified for ANPR only where the measured scale clears "
        "**250 px/m** (IEC DORI *Identify*) — Prahari **refuses** to guess a plate it "
        "cannot resolve, rather than emit garbage. Where it can, a hybrid reader "
        "(fast-alpr detection + **Awiros PP-OCRv5**, an Apache-2.0 Indian specialist) "
        "reads the characters.")
    clip = ASSETS / "CAM-011.mp4"
    if clip.exists():
        st.video(str(clip))
    st.markdown("Sample live reads from the footage:")
    st.code("MP 04 YK 621   ·   KA 51 A 0120   ·   TN 55 3504   ·   HP …   ·   KA 66 0",
            language=None)
    st.caption("MP = Madhya Pradesh · KA = Karnataka · TN = Tamil Nadu · HP = Himachal "
               "Pradesh. Non-plate text (shop boards, hoardings) is dropped by the "
               "Indian-plate format check.")

# --------------------------------------------------------------- Benchmarks ---
with tabs[4]:
    st.subheader("Measured results")
    st.caption("Every number is reproducible from a script in the repo. Nothing here "
               "is a projection.")
    st.dataframe(pd.DataFrame(
        [{"Capability": a, "Result": b, "Kind": c, "Note": d}
         for a, b, c, d in core.BENCHMARKS],
    ), hide_index=True, use_container_width=True)
    st.caption("Honesty stance: negative results are kept visible. The Event Priority "
               "Score is a ranking aid, not a calibrated probability, and has not been "
               "validated against border ground truth.")

# ----------------------------------------------------------------- Security ---
with tabs[5]:
    st.subheader("Security posture (STRIDE)")
    st.caption("The one honest headline: the ledger is tamper-**resistant** via core "
               "countersigning today, on a symmetric-HMAC trust root. HSM-backed "
               "asymmetric signing and external anchoring are the next steps — "
               "designed for, not claimed as built.")
    for name, status, detail in core.SECURITY:
        cls = "ok" if "BUILT" in status else ""
        st.markdown(f"<span class='pill {cls}'>{status}</span> **{name}** — {detail}",
                    unsafe_allow_html=True)
    st.write("")
    st.markdown(f"Full code, tests and threat model: **[github.com/sayaksatpathi/PRAHARi]({GITHUB})**")

st.divider()
st.caption("Prahari · SIH26187 · showcase runs free on Hugging Face Spaces (CPU). "
           "The full real-time GPU pipeline runs on an edge node — see the repo.")
