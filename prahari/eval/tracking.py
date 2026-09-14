"""CLEAR-MOT and IDF1 scoring for the tracker.

Detection precision and recall say whether the detector saw the people. They say
nothing about whether the tracker kept them as the *same* people, and for Prahari
that second question is the load-bearing one: loitering is dwell time on one
track, cross-camera handoff is an identity leaving one camera, patrol conformance
is a heading accumulated over a trajectory. Every one of those is wrong if the
identity broke and the system cannot tell.

Two standard families, because they answer different questions and disagreeing is
informative:

**CLEAR-MOT** (Bernardin & Stiefelhagen 2008) scores frame by frame.

    MOTA = 1 - (FN + FP + IDSW) / GT

It is dominated by detection errors, and it can go negative - a detector that
hallucinates more boxes than there are people scores below zero, which is the
correct verdict and worth not hiding. MOTP is the mean localisation IoU of the
matches, so it says how tight the boxes are, independently of how many were
found.

**IDF1** (Ristani et al. 2016) scores identities globally rather than per frame:
it finds the best one-to-one assignment between ground-truth and predicted
tracks over the whole sequence, then reports an F1 over correctly-identified
detections. A tracker that fragments one person into ten tracks can hold a
respectable MOTA and a poor IDF1, and that gap is exactly the failure that breaks
dwell time and handoff.

Matching follows the standard: matches from the previous frame are preserved
while they still overlap, and only the remainder is re-assigned. Without that
carry-forward, every frame re-solves the assignment from scratch and ID switches
are wildly overcounted.

`scipy` is used for optimal assignment when present and falls back to a greedy
pass when it is not - an eval-only nicety that never reaches the edge node. The
fallback is reported in the result rather than hidden, because greedy IDF1 is a
lower bound, not the metric.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

log = logging.getLogger("prahari.eval.tracking")

# The MOTChallenge convention for pedestrian tracking.
DEFAULT_IOU = 0.5
# A ground-truth track is Mostly Tracked at >= 80% coverage, Mostly Lost at
# <= 20%, Partially Tracked in between.
MT_RATIO = 0.8
ML_RATIO = 0.2


def _iou(a: Sequence[float], b: Sequence[float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    union = ((ax2 - ax1) * (ay2 - ay1)) + ((bx2 - bx1) * (by2 - by1)) - inter
    return inter / union if union > 0 else 0.0


def _assign(cost: list[list[float]], maximise: bool) -> list[tuple[int, int]]:
    """Optimal one-to-one assignment, or a greedy one if scipy is absent."""
    if not cost or not cost[0]:
        return []
    try:
        import numpy as np
        from scipy.optimize import linear_sum_assignment

        matrix = np.asarray(cost, dtype=float)
        rows, cols = linear_sum_assignment(-matrix if maximise else matrix)
        return list(zip(rows.tolist(), cols.tolist()))
    except Exception:
        pairs = [(r, c, cost[r][c])
                 for r in range(len(cost)) for c in range(len(cost[0]))]
        pairs.sort(key=lambda p: p[2], reverse=maximise)
        used_r: set[int] = set()
        used_c: set[int] = set()
        out = []
        for r, c, _ in pairs:
            if r in used_r or c in used_c:
                continue
            used_r.add(r)
            used_c.add(c)
            out.append((r, c))
        return out


def _have_scipy() -> bool:
    try:
        import scipy.optimize  # noqa: F401

        return True
    except Exception:
        return False


@dataclass
class TrackingScorer:
    """Accumulates CLEAR-MOT and IDF1 counts over a sequence.

    Fed one frame at a time with ground-truth and predicted (id, box) pairs, so
    it runs alongside the existing detection scoring on a single pass through
    the footage rather than needing the whole sequence in memory.
    """
    iou_threshold: float = DEFAULT_IOU

    frames: int = 0
    gt_total: int = 0
    pred_total: int = 0
    matches: int = 0
    misses: int = 0                       # false negatives
    false_positives: int = 0
    id_switches: int = 0
    iou_sum: float = 0.0

    # gt_id -> the pred_id it was last matched to, for switch detection.
    _last_match: dict[Any, Any] = field(default_factory=dict)
    # gt_id -> [frames present, frames matched], for MT/ML.
    _coverage: dict[Any, list[int]] = field(default_factory=dict)
    # (gt_id, pred_id) -> frames they were matched, for IDF1.
    _pairs: dict[tuple[Any, Any], int] = field(default_factory=lambda: defaultdict(int))
    _gt_counts: dict[Any, int] = field(default_factory=lambda: defaultdict(int))
    _pred_counts: dict[Any, int] = field(default_factory=lambda: defaultdict(int))

    def update(self,
               ground_truth: Iterable[tuple[Any, Sequence[float]]],
               predictions: Iterable[tuple[Any, Sequence[float]]]) -> None:
        gts = list(ground_truth)
        preds = list(predictions)
        self.frames += 1
        self.gt_total += len(gts)
        self.pred_total += len(preds)

        for gid, _ in gts:
            self._gt_counts[gid] += 1
            self._coverage.setdefault(gid, [0, 0])[0] += 1
        for pid, _ in preds:
            self._pred_counts[pid] += 1

        unmatched_g = set(range(len(gts)))
        unmatched_p = set(range(len(preds)))
        frame_matches: list[tuple[int, int, float]] = []

        # 1. Carry forward last frame's pairings while they still overlap. This
        #    is what stops a tracker being charged an ID switch every time the
        #    assignment has a tie it could have broken either way.
        for gi, (gid, gbox) in enumerate(gts):
            pid = self._last_match.get(gid)
            if pid is None:
                continue
            for pi, (cand_pid, pbox) in enumerate(preds):
                if cand_pid != pid or pi not in unmatched_p:
                    continue
                overlap = _iou(gbox, pbox)
                if overlap >= self.iou_threshold:
                    frame_matches.append((gi, pi, overlap))
                    unmatched_g.discard(gi)
                    unmatched_p.discard(pi)
                break

        # 2. Optimally assign whatever is left.
        rem_g = sorted(unmatched_g)
        rem_p = sorted(unmatched_p)
        if rem_g and rem_p:
            cost = [[_iou(gts[gi][1], preds[pi][1]) for pi in rem_p] for gi in rem_g]
            for r, c in _assign(cost, maximise=True):
                if cost[r][c] >= self.iou_threshold:
                    gi, pi = rem_g[r], rem_p[c]
                    frame_matches.append((gi, pi, cost[r][c]))
                    unmatched_g.discard(gi)
                    unmatched_p.discard(pi)

        # 3. Tally.
        for gi, pi, overlap in frame_matches:
            gid = gts[gi][0]
            pid = preds[pi][0]
            previous = self._last_match.get(gid)
            if previous is not None and previous != pid:
                self.id_switches += 1
            self._last_match[gid] = pid
            self.matches += 1
            self.iou_sum += overlap
            self._coverage[gid][1] += 1
            self._pairs[(gid, pid)] += 1

        self.misses += len(unmatched_g)
        self.false_positives += len(unmatched_p)

    # -- reporting -------------------------------------------------------
    @property
    def mota(self) -> float:
        if not self.gt_total:
            return 0.0
        return 1.0 - (self.misses + self.false_positives + self.id_switches) / self.gt_total

    @property
    def motp(self) -> float:
        return self.iou_sum / self.matches if self.matches else 0.0

    def _idf1(self) -> tuple[float, int, int, int]:
        """Best one-to-one identity assignment over the whole sequence."""
        if not self._pairs:
            return 0.0, 0, self.gt_total, self.pred_total
        gt_ids = sorted({g for g, _ in self._pairs}, key=str)
        pred_ids = sorted({p for _, p in self._pairs}, key=str)
        cost = [[self._pairs.get((g, p), 0) for p in pred_ids] for g in gt_ids]
        idtp = sum(cost[r][c] for r, c in _assign(cost, maximise=True))
        idfn = self.gt_total - idtp
        idfp = self.pred_total - idtp
        denom = 2 * idtp + idfp + idfn
        return ((2 * idtp / denom) if denom else 0.0), idtp, idfn, idfp

    def track_quality(self) -> dict[str, int]:
        mt = pt = ml = 0
        for present, matched in self._coverage.values():
            ratio = matched / present if present else 0.0
            if ratio >= MT_RATIO:
                mt += 1
            elif ratio <= ML_RATIO:
                ml += 1
            else:
                pt += 1
        return {"mostly_tracked": mt, "partially_tracked": pt, "mostly_lost": ml}

    def as_dict(self) -> dict[str, Any]:
        idf1, idtp, idfn, idfp = self._idf1()
        quality = self.track_quality()
        return {
            "frames_scored": self.frames,
            "iou_threshold": self.iou_threshold,
            "gt_detections": self.gt_total,
            "predicted_detections": self.pred_total,
            "matches": self.matches,
            "misses_fn": self.misses,
            "false_positives": self.false_positives,
            "id_switches": self.id_switches,
            "mota": round(self.mota, 4),
            "motp_iou": round(self.motp, 4),
            "idf1": round(idf1, 4),
            "idtp": idtp, "idfn": idfn, "idfp": idfp,
            "gt_tracks": len(self._coverage),
            "predicted_tracks": len(self._pred_counts),
            **quality,
            "assignment": "optimal (scipy)" if _have_scipy() else
                          "greedy (scipy absent; IDF1 is a lower bound)",
            "note": ("CLEAR-MOT and IDF1 over this sequence. MOTA is dominated by "
                     "detection errors and is negative when false positives plus "
                     "misses exceed the ground-truth count; that is the metric "
                     "behaving correctly, not a bug. MOTP is mean IoU over "
                     "matched pairs, so it reports box tightness independently "
                     "of how many were found."),
        }


def tracks_as_pairs(tracks, observed_only: bool = True
                    ) -> list[tuple[Any, list[float]]]:
    """(track_id, [x1,y1,x2,y2]) for the scorer, from pipeline Track objects.

    `observed_only` excludes tracks that were not matched to a detection on this
    frame (`lost_frames > 0`), and defaults to on. The distinction matters and is
    worth stating, because it changes the numbers by a large factor.

    ByteTrack keeps a lost track alive internally for up to `max_lost_frames` so
    it can be re-associated when the object reappears - correct behaviour, and
    the whole point of two-stage association. But those coasting tracks are
    *hypotheses*, not assertions that the object is visible. Scoring them as
    output claims charged the tracker a false positive on every frame of every
    coast: on one simulated camera that turned 4,463 real objects into 23,375
    predicted boxes and 15 ground-truth tracks into 284 predicted ones, for a
    MOTA of -4.07 that measured the eval harness rather than the tracker.

    The pipeline itself already draws this line - profiling only accepts
    observations with `lost_frames == 0`, because a coasted box is an estimate
    and feeding estimates to a calibration fit injects the tracker's own drift.
    The scorer now draws it in the same place.

    Pass `observed_only=False` to score the full hypothesis set instead.
    """
    out = []
    for t in tracks:
        if observed_only and getattr(t, "lost_frames", 0) > 0:
            continue
        b = t.bbox
        out.append((t.track_id, [b.x1, b.y1, b.x2, b.y2]))
    return out


def ground_truth_as_pairs(entries, object_class: str = "person"
                          ) -> list[tuple[Any, list[float]]]:
    """(track_id, box) for GT entries that carry an identity.

    Two sources, two key names for the same concept: MOT annotations carry
    `track_id`, the simulator carries `actor_id`. Both are a persistent identity
    for one object across frames, so both are accepted.

    Entries with neither are skipped rather than given a synthetic id: a
    fabricated identity would silently turn every frame into an ID switch, and
    the resulting IDF1 would be a number about nothing.
    """
    out = []
    for e in entries or []:
        if object_class and e.get("object_class") != object_class:
            continue
        tid = e.get("track_id")
        if tid is None:
            tid = e.get("actor_id")
        if tid is None:
            continue
        out.append((tid, list(e["bbox"])))
    return out
