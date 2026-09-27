"""Prahari — Border Video Intelligence (Streamlit Cloud).

Renders the exact Prahari showcase UI: the same tabbed page as the Hugging Face
Static Space — Overview, Live cameras, Evidence integrity (live), ANPR,
Benchmarks and Security — with the tamper-evident ledger simulation running
live in the browser (real SHA-256 + HMAC via the Web Crypto API). The clips are
served from the public HF Space so this stays a byte-for-byte match.
"""
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

st.set_page_config(page_title="Prahari — Border Video Intelligence",
                   page_icon="🛡️", layout="wide")

# Strip Streamlit's default chrome/padding so the embedded page fills the view.
st.markdown(
    """
    <style>
      #MainMenu, header, footer {visibility: hidden;}
      .stApp {background: #0b1017;}
      .block-container {padding: 0 !important; max-width: 100% !important;}
      [data-testid="stAppViewBlockContainer"],
      [data-testid="stMainBlockContainer"] {padding: 0 !important;}
      [data-testid="stHeader"] {display: none;}
      iframe {border: none;}
    </style>
    """,
    unsafe_allow_html=True,
)

html = Path(__file__).with_name("index.html").read_text(encoding="utf-8")
components.html(html, height=2200, scrolling=True)
