"""Video-anomaly-detection metrics — the official Street Scene evaluation protocol.

Implements the three criteria from:

    B. Ramachandra and M. Jones, "Street Scene: A new dataset and evaluation
    protocol for video anomaly detection", WACV 2020.

    * Frame-level ROC-AUC — the long-standing frame criterion (a frame is
      positive if it contains any anomaly; sweep a threshold on the frame anomaly
      score; area under the ROC curve).
    * RBDC (Region-Based Detection Criterion) — area under the curve of
      region-detection rate vs. false positives per frame, integrated over
      [0, 1] FP/frame and normalised. A predicted region matches a ground-truth
      region when IoU >= alpha (paper default 0.1).
    * TBDC (Track-Based Detection Criterion) — same curve but the y-axis is
      track-detection rate: each ground-truth track contributes the fraction of
      its frames whose region is detected.

These are dataset-agnostic: give them per-frame ground-truth regions, ground-truth
tracks, and per-frame predicted (region, score) pairs, and they return the same
numbers the Street Scene protocol reports — on Street Scene itself, on UCSD Ped2,
Avenue, or any dataset in that layout.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from sklearn.metrics import roc_auc_score

# Paper defaults.
IOU_MATCH = 0.1          # a predicted region matches a GT region at IoU >= this
FP_CAP = 1.0             # integrate the RBDC/TBDC curve over [0, 1] FP per frame


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """IoU of two [x1, y1, x2, y2] boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return float(inter / union) if union > 0 else 0.0


def frame_level_auc(frame_scores: list[float], frame_labels: list[int]) -> float:
    """Standard frame-level ROC-AUC. labels: 1 = anomalous frame, 0 = normal."""
    y = np.asarray(frame_labels)
    if y.min() == y.max():
        return float("nan")     # AUC undefined with a single class
    return float(roc_auc_score(y, np.asarray(frame_scores, dtype=float)))


@dataclass
class Track:
    """A ground-truth anomalous track: one region box per frame it appears in."""
    boxes: dict[int, np.ndarray] = field(default_factory=dict)   # frame_idx -> box

    @property
    def length(self) -> int:
        return len(self.boxes)


def _curve_area(fp_per_frame: np.ndarray, tpr: np.ndarray, cap: float = FP_CAP) -> float:
    """Normalised area under TPR vs FP/frame, integrated over [0, cap].

    Points come in as the detection threshold decreases (FP/frame increases from
    0). Sort by FP/frame, clip/interpolate at the cap, trapezoid-integrate, and
    divide by cap so a perfect detector (TPR=1 at FP=0) scores 1.0.
    """
    order = np.argsort(fp_per_frame)
    x = fp_per_frame[order]
    y = tpr[order]
    # Prepend the origin so the area from 0 is counted.
    x = np.concatenate(([0.0], x))
    y = np.concatenate(([0.0], y))
    # Clip to the cap, interpolating TPR exactly at x = cap.
    if x[-1] > cap:
        y_at_cap = float(np.interp(cap, x, y))
        keep = x < cap
        x = np.concatenate((x[keep], [cap]))
        y = np.concatenate((y[keep], [y_at_cap]))
    elif x[-1] < cap:
        # Extend the last (highest-recall) point flat out to the cap.
        x = np.concatenate((x, [cap]))
        y = np.concatenate((y, [y[-1]]))
    return float(np.trapz(y, x) / cap)


def rbdc_tbdc(
    gt_regions_per_frame: list[list[np.ndarray]],
    gt_tracks: list[Track],
    pred_per_frame: list[list[tuple[np.ndarray, float]]],
    iou_match: float = IOU_MATCH,
    fp_cap: float = FP_CAP,
) -> dict[str, float]:
    """Compute RBDC and TBDC over a sweep of the detection-score threshold.

    gt_regions_per_frame[f]  : list of GT anomaly boxes in frame f
    gt_tracks                : list of Track (GT region per frame across its life)
    pred_per_frame[f]        : list of (box, score) predicted anomaly regions in f
    """
    num_frames = len(pred_per_frame)
    total_gt_regions = sum(len(r) for r in gt_regions_per_frame)
    num_tracks = len(gt_tracks)

    all_scores = sorted({s for frame in pred_per_frame for (_, s) in frame},
                        reverse=True)
    if not all_scores or total_gt_regions == 0 or num_tracks == 0:
        return {"rbdc": 0.0, "tbdc": 0.0, "thresholds": 0,
                "gt_regions": total_gt_regions, "gt_tracks": num_tracks}

    region_tpr, track_tpr, fp_rate = [], [], []

    for thr in all_scores:
        detected_gt_regions = 0
        false_positives = 0
        # Track which (frame, gt-region-index) got detected, for TBDC.
        detected_map: dict[int, set[int]] = {}

        for f, preds in enumerate(pred_per_frame):
            active = [box for (box, s) in preds if s >= thr]
            gts = gt_regions_per_frame[f]
            gt_hit = [False] * len(gts)
            for box in active:
                matched = False
                for gi, gbox in enumerate(gts):
                    if iou(box, gbox) >= iou_match:
                        matched = True
                        if not gt_hit[gi]:
                            gt_hit[gi] = True
                            detected_gt_regions += 1
                            detected_map.setdefault(f, set()).add(gi)
                if not matched:
                    false_positives += 1

        # TBDC: each GT track contributes the fraction of its frames detected.
        track_credit = 0.0
        for tr in gt_tracks:
            hit = 0
            for f, tbox in tr.boxes.items():
                gts = gt_regions_per_frame[f]
                # Which GT-region index in frame f is this track's box?
                idx = _closest_region(tbox, gts)
                if idx is not None and idx in detected_map.get(f, set()):
                    hit += 1
            if tr.length > 0:
                track_credit += hit / tr.length

        region_tpr.append(detected_gt_regions / total_gt_regions)
        track_tpr.append(track_credit / num_tracks)
        fp_rate.append(false_positives / max(num_frames, 1))

    rbdc = _curve_area(np.asarray(fp_rate), np.asarray(region_tpr), fp_cap)
    tbdc = _curve_area(np.asarray(fp_rate), np.asarray(track_tpr), fp_cap)
    return {"rbdc": rbdc, "tbdc": tbdc, "thresholds": len(all_scores),
            "gt_regions": total_gt_regions, "gt_tracks": num_tracks}


def _closest_region(box: np.ndarray, gts: list[np.ndarray]) -> int | None:
    """Index of the GT region in this frame that a track's box corresponds to."""
    best, best_iou = None, 0.0
    for i, g in enumerate(gts):
        v = iou(box, g)
        if v > best_iou:
            best, best_iou = i, v
    return best if best_iou > 0.5 else (best if best_iou > 0 else None)


# --- mask -> regions -> tracks helpers (for datasets with pixel-mask GT) ------

def regions_from_mask(mask: np.ndarray, min_area: int = 8) -> list[np.ndarray]:
    """Connected components of a binary mask -> [x1, y1, x2, y2] boxes."""
    import cv2
    m = (mask > 0).astype(np.uint8)
    n, _, stats, _ = cv2.connectedComponentsWithStats(m, connectivity=8)
    boxes = []
    for i in range(1, n):                      # 0 is background
        x, y, w, h, area = stats[i]
        if area >= min_area:
            boxes.append(np.array([x, y, x + w, y + h], dtype=float))
    return boxes


def link_tracks(gt_regions_per_frame: list[list[np.ndarray]],
                iou_link: float = 0.3) -> list[Track]:
    """Link per-frame GT regions into tracks by IoU overlap across frames."""
    tracks: list[Track] = []
    active: list[Track] = []
    for f, regions in enumerate(gt_regions_per_frame):
        used = [False] * len(regions)
        still_active: list[Track] = []
        for tr in active:
            prev = tr.boxes[max(tr.boxes)]
            best_i, best_v = None, iou_link
            for i, r in enumerate(regions):
                if used[i]:
                    continue
                v = iou(prev, r)
                if v >= best_v:
                    best_i, best_v = i, v
            if best_i is not None:
                tr.boxes[f] = regions[best_i]
                used[best_i] = True
                still_active.append(tr)
            else:
                tracks.append(tr)              # track ended
        for i, r in enumerate(regions):
            if not used[i]:
                t = Track({f: r})
                still_active.append(t)
        active = still_active
    tracks.extend(active)
    return tracks
