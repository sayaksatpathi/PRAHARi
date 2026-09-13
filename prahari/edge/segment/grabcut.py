"""GrabCut segmenter — a real, CPU-only, download-free segmentation backend.

This is not a placeholder. OpenCV's GrabCut is genuine box-prompted foreground
segmentation: given a rectangle, it models foreground and background colour
distributions and cuts the object out with a graph min-cut. It produces a real
silhouette, real ground contact and a real zone-intersection fraction, on the CPU,
with nothing to download.

Its role is twofold. It is the honest default that lets the entire event-triggered
refinement pipeline run and be tested where SAM's weights and a GPU cannot be
obtained, and it is a legitimate fallback in its own right: on a fanless edge box
with no accelerator, a promptable classical segmenter invoked only on important
events is a defensible engineering choice, not a stopgap.

It is slower and blunter than SAM 2 - a few tens of milliseconds per object, and
it can bleed into similar-coloured background - so it is marked `approximate` and
the report says so. What it is not is fake.
"""
from __future__ import annotations

import time

import cv2
import numpy as np

from prahari.common.models import BBox
from prahari.edge.segment.base import SegmentResult, Segmenter, finalise_mask


class GrabCutSegmenter(Segmenter):
    name = "grabcut (cpu, approximate)"
    approximate = True

    def __init__(self, iterations: int = 3, pad_ratio: float = 0.08,
                 max_side: int = 320) -> None:
        # A little padding around the prompt box gives GrabCut background pixels
        # to model; with none, it has only foreground and cuts poorly.
        self.iterations = iterations
        self.pad_ratio = pad_ratio
        # Cap the working resolution: GrabCut cost grows with pixel count, and
        # this stage must stay cheap enough to run on an event without stalling
        # the pipeline. The mask is scaled back up afterwards.
        self.max_side = max_side

    def segment(self, image: np.ndarray, box: BBox) -> SegmentResult | None:
        h, w = image.shape[:2]
        pad_x = box.width * self.pad_ratio
        pad_y = box.height * self.pad_ratio
        x1 = int(max(0, box.x1 - pad_x)); y1 = int(max(0, box.y1 - pad_y))
        x2 = int(min(w, box.x2 + pad_x)); y2 = int(min(h, box.y2 + pad_y))
        if x2 - x1 < 8 or y2 - y1 < 8:
            return None

        crop = image[y1:y2, x1:x2]
        ch, cw = crop.shape[:2]

        # Downscale the working crop if large.
        scale = min(1.0, self.max_side / max(ch, cw))
        work = cv2.resize(crop, (max(1, int(cw * scale)), max(1, int(ch * scale)),),
                          interpolation=cv2.INTER_AREA) if scale < 1.0 else crop

        t0 = time.perf_counter()
        mask = np.zeros(work.shape[:2], np.uint8)
        # Seed: the padded margin is probable background, the prompt box interior
        # probable foreground.
        mask[:] = cv2.GC_PR_BGD
        mx1 = int(pad_x * scale); my1 = int(pad_y * scale)
        mx2 = work.shape[1] - mx1; my2 = work.shape[0] - my1
        if mx2 - mx1 < 4 or my2 - my1 < 4:
            return None
        mask[my1:my2, mx1:mx2] = cv2.GC_PR_FGD

        bgd = np.zeros((1, 65), np.float64)
        fgd = np.zeros((1, 65), np.float64)
        try:
            cv2.grabCut(work, mask, None, bgd, fgd, self.iterations,
                        cv2.GC_INIT_WITH_MASK)
        except cv2.error:
            return None
        latency = (time.perf_counter() - t0) * 1000.0

        fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 1, 0).astype(np.uint8)
        if scale < 1.0:
            fg = cv2.resize(fg, (cw, ch), interpolation=cv2.INTER_NEAREST)

        # Largest connected component only: GrabCut can leave speckle.
        n, labels, stats, _ = cv2.connectedComponentsWithStats(fg, connectivity=8)
        if n > 1:
            largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
            fg = (labels == largest).astype(np.uint8)

        return finalise_mask(
            fg, offset=(x1, y1), backend=self.name,
            approximate=True, latency_ms=latency,
            note="GrabCut box-prompted segmentation (CPU); a real silhouette but "
                 "blunter than SAM and prone to colour bleed.",
        )

    def warmup(self) -> None:
        dummy = np.full((40, 40, 3), 127, np.uint8)
        self.segment(dummy, BBox(x1=8, y1=8, x2=32, y2=32))
