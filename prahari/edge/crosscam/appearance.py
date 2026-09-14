"""Appearance signatures for cross-camera re-identification.

Linking a track that left one camera to a track that appeared at another needs
more than timing — two people can cross the same corridor a minute apart. A
re-identification model would do this best, but a real one is a download this
build cannot make and a dependency the edge story would rather avoid. So the cue
here is deliberately lightweight and honest about it: a colour-and-tone signature
of the object, cheap to compute and to compare, that narrows candidates rather
than deciding them on its own.

An HSV histogram, split top-half / bottom-half, captures the coarse thing that
actually distinguishes people across cameras at a distance — a dark jacket over
light trousers, say — without pretending to the discrimination of a trained
descriptor. It is used as one factor among timing and object class, never alone,
and the coordinator treats a weak appearance match as weak evidence.

This is coarse on purpose, and the docs say so. On a thermal or night-IR camera,
where colour is absent, it degrades to a tone signature and the coordinator leans
harder on timing and class.
"""
from __future__ import annotations

import numpy as np

# Histogram bins. Small: the signature must be cheap to store per track and per
# open handoff, and fine bins would chase noise a distant, low-res crop does not
# actually carry.
H_BINS = 8
S_BINS = 4
V_BINS = 4
_HALF_LEN = H_BINS * S_BINS * V_BINS


def signature(image: np.ndarray, box) -> np.ndarray | None:
    """A normalised top/bottom HSV histogram for the object in `box`.

    Returns a 1-D float32 vector, or None if the crop is too small to be
    meaningful. The vector concatenates the upper-body and lower-body histograms
    so that vertical colour structure (jacket vs trousers) is preserved.
    """
    import cv2

    h, w = image.shape[:2]
    x1 = int(max(0, box.x1)); y1 = int(max(0, box.y1))
    x2 = int(min(w, box.x2)); y2 = int(min(h, box.y2))
    if x2 - x1 < 6 or y2 - y1 < 12:
        return None

    crop = image[y1:y2, x1:x2]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    mid = hsv.shape[0] // 2

    parts = []
    for region in (hsv[:mid], hsv[mid:]):
        hist = cv2.calcHist([region], [0, 1, 2], None,
                            [H_BINS, S_BINS, V_BINS],
                            [0, 180, 0, 256, 0, 256])
        hist = hist.flatten().astype(np.float32)
        total = hist.sum()
        if total > 0:
            hist /= total
        parts.append(hist)
    return np.concatenate(parts)


def similarity(a: np.ndarray | None, b: np.ndarray | None) -> float:
    """Appearance similarity in [0, 1]. 0 when either signature is missing.

    Histogram intersection, averaged over the two body halves. Robust to the
    small lighting shifts between cameras and cheap enough to run against every
    open handoff candidate.
    """
    if a is None or b is None or a.shape != b.shape:
        return 0.0
    inter = np.minimum(a, b).sum()
    # Each half is L1-normalised, so a perfect match sums to 2 across both halves.
    return float(inter / 2.0)


def blend(existing: np.ndarray | None, new: np.ndarray | None,
          alpha: float = 0.3) -> np.ndarray | None:
    """Exponentially update an entity's signature as it is re-observed.

    A single crop can be unlucky - motion blur, a passing occlusion - so an
    entity's signature is a running average of its sightings rather than whatever
    the latest frame happened to show.
    """
    if new is None:
        return existing
    if existing is None:
        return new
    out = (1 - alpha) * existing + alpha * new
    s = out.sum()
    return (out / s * 2.0).astype(np.float32) if s > 0 else out
