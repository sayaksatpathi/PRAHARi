"""Segmentation abstraction — the second-stage precision module.

Segmentation is deliberately NOT the detector. The detector and tracker run
continuously and cheaply on every frame; segmentation is invoked only when an
event candidate is important enough to be worth the cost, prompted by the box
that detection and tracking already produced. This is the event-triggered
refinement architecture: a lightweight first stage that decides *what is worth
looking at*, and an expensive second stage that looks at it precisely.

What a mask buys over a box, and why it is worth a second stage at all:

  * **Ground contact.** Zone membership is tested on where an object meets the
    ground. A bounding box's bottom edge is a poor estimate of that when the box
    is loose or the subject is partly occluded; the lowest point of the actual
    silhouette is not.

  * **Zone intersection as a fraction, not a yes/no.** A box either overlaps a
    restricted polygon or it does not. A mask says *how much* of the real object
    is inside it — a confirmation signal that a box cannot give.

  * **Evidence.** A precise outline on the evidence frame is far easier for an
    operator to verify than a rectangle that also contains fence, ground and sky.

Backends sit behind this interface exactly as detectors do: a real SAM 2 / ONNX
model where the weights and GPU are available, a real CPU method (GrabCut) that
needs no download so the whole pipeline runs and is testable, and a degenerate
box-as-mask fallback that preserves current behaviour when segmentation is off.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from prahari.common.models import BBox, Point


@dataclass
class SegmentResult:
    """A segmentation of one object, kept bbox-local to bound memory.

    The mask is stored cropped to its bounding region with an offset back into
    the full frame, because a full-frame mask per event on a dozen cameras is a
    memory cost with no benefit - the object occupies a small part of the frame.
    """
    mask: np.ndarray                 # uint8 {0,1}, HxW of the crop region
    offset: tuple[int, int]          # (x, y) of the crop's top-left in the frame
    area_px: int
    polygon: list[Point]             # simplified outer contour, full-frame coords
    ground_contact: tuple[float, float]   # lowest silhouette point (true foot)
    backend: str
    latency_ms: float = 0.0
    approximate: bool = False        # True for non-SAM backends
    score: float = 0.0               # backend's own confidence, if any
    note: str = ""

    def contains_fraction(self, in_mask_predicate) -> float:
        """Fraction of mask pixels satisfying a predicate on full-frame coords.

        `in_mask_predicate(x, y) -> bool` is called on the centre of each set
        pixel. Used for mask-in-zone fraction without materialising a second
        full-frame array.
        """
        ys, xs = np.nonzero(self.mask)
        if len(xs) == 0:
            return 0.0
        ox, oy = self.offset
        inside = 0
        # Sample rather than test every pixel when the mask is large: a few
        # thousand points estimate the fraction to well within rounding, and a
        # refinement signal does not need exactness.
        n = len(xs)
        step = max(1, n // 4000)
        counted = 0
        for i in range(0, n, step):
            counted += 1
            if in_mask_predicate(ox + int(xs[i]), oy + int(ys[i])):
                inside += 1
        return inside / counted if counted else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "area_px": self.area_px,
            "approximate": self.approximate,
            "latency_ms": round(self.latency_ms, 1),
            "ground_contact": [round(self.ground_contact[0], 1),
                               round(self.ground_contact[1], 1)],
            "polygon": [[round(p.x, 1), round(p.y, 1)] for p in self.polygon],
            "score": round(self.score, 3),
            "note": self.note,
        }


class Segmenter(abc.ABC):
    """Turns a box prompt into a mask."""

    name: str = "segmenter"
    device: str = "cpu"
    approximate: bool = False

    @abc.abstractmethod
    def segment(self, image: np.ndarray, box: BBox) -> SegmentResult | None:
        """Segment the object in `box`. Returns None if nothing could be found."""

    def warmup(self) -> None:
        return None

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "device": self.device,
                "approximate": self.approximate}


# =====================================================================
# Shared post-processing
# =====================================================================

def finalise_mask(mask_crop: np.ndarray, offset: tuple[int, int],
                  backend: str, *, approximate: bool, latency_ms: float,
                  score: float = 0.0, note: str = "") -> SegmentResult | None:
    """Turn a binary crop mask into a SegmentResult: contour, area, foot point.

    Shared by every backend so the downstream contract (polygon in full-frame
    coordinates, ground-contact point, area) is identical regardless of how the
    mask was produced.
    """
    import cv2

    mask_crop = (mask_crop > 0).astype(np.uint8)
    area = int(mask_crop.sum())
    if area < 4:
        return None

    ox, oy = offset
    contours, _ = cv2.findContours(mask_crop, cv2.RETR_EXTERNAL,
                                   cv2.CHAIN_APPROX_SIMPLE)
    polygon: list[Point] = []
    if contours:
        biggest = max(contours, key=cv2.contourArea)
        eps = 0.01 * cv2.arcLength(biggest, True)
        approx = cv2.approxPolyDP(biggest, eps, True)
        polygon = [Point(x=float(p[0][0] + ox), y=float(p[0][1] + oy))
                   for p in approx]

    # Ground contact: the lowest set pixel, averaged over that bottom row, is a
    # far better foot estimate than the box bottom.
    ys, xs = np.nonzero(mask_crop)
    bottom = ys.max()
    foot_xs = xs[ys == bottom]
    ground_contact = (float(foot_xs.mean() + ox), float(bottom + oy))

    return SegmentResult(
        mask=mask_crop, offset=offset, area_px=area, polygon=polygon,
        ground_contact=ground_contact, backend=backend, latency_ms=latency_ms,
        approximate=approximate, score=score, note=note,
    )
