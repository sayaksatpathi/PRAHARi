"""Segmentation backend selection.

The selection is explicit and logged, like the detector's, so "which segmenter
is actually running, and is it the real one?" always has a clear answer. The
order of preference:

    off        -> no segmentation; events keep their box-based behaviour
    sam_onnx   -> real SAM 2 via ONNX, if the weights are present
    grabcut    -> real CPU segmentation, no download (the tested default)

`auto` prefers SAM when its weights exist and otherwise uses GrabCut, so a node
with the weights installed gets the good backend and a node without one still
gets working, tested refinement rather than nothing.
"""
from __future__ import annotations

import logging
from pathlib import Path

from prahari.edge.segment.base import Segmenter
from prahari.edge.segment.grabcut import GrabCutSegmenter

log = logging.getLogger("prahari.segment.factory")


def build_segmenter(settings) -> Segmenter | None:
    """Construct a segmenter, or None when segmentation is disabled."""
    choice = (getattr(settings, "segment_backend", "auto") or "auto").lower()
    if choice == "off":
        log.info("segmentation: disabled (events keep box-based behaviour)")
        return None

    enc = Path(getattr(settings, "sam_encoder_path", "models/sam2_encoder.onnx"))
    dec = Path(getattr(settings, "sam_decoder_path", "models/sam2_decoder.onnx"))
    sam_available = enc.exists() and dec.exists()

    if choice in ("sam_onnx", "sam", "sam2"):
        return _build_sam(enc, dec, settings, required=True)

    if choice == "grabcut":
        log.info("segmentation: GrabCut (CPU, approximate) by configuration")
        return GrabCutSegmenter()

    # auto
    if sam_available:
        seg = _build_sam(enc, dec, settings, required=False)
        if seg is not None:
            return seg
    log.info("segmentation: no SAM weights present - using GrabCut (CPU, "
             "approximate). Install SAM 2 ONNX weights (docs/segmentation.md) "
             "for the production backend.")
    return GrabCutSegmenter()


def _build_sam(enc: Path, dec: Path, settings, required: bool) -> Segmenter | None:
    try:
        from prahari.edge.segment.sam_onnx import SamOnnxSegmenter

        seg = SamOnnxSegmenter(enc, dec, device=getattr(settings, "device", "auto"),
                               cuda_dll_dir=getattr(settings, "cuda_dll_dir", None))
        log.warning("segmentation: SAM 2 ONNX loaded but UNVALIDATED against real "
                    "weights on this machine - verify its masks on first real run "
                    "(docs/segmentation.md)")
        return seg
    except Exception as exc:
        if required:
            raise
        log.warning("segmentation: SAM 2 ONNX failed to load (%s); falling back "
                    "to GrabCut", exc)
        return None
