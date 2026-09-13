"""MOTChallenge (MOT16/17/20) sequence adapter.

Lets the evaluation harness run against real footage with real ground truth. A
MOT sequence is a directory of numbered frames plus a `gt/gt.txt` of annotated
boxes, which is exactly what the harness needs to compute genuine detection
recall and precision against a real model - the thing synthetic footage cannot
provide, because a model trained on photographs sees nothing in a rendered scene.

Sequence layout (MOT17):

    MOT17-04-FRCNN/
        seqinfo.ini      imWidth, imHeight, seqLength, imDir, imExt, frameRate
        img1/000001.jpg  numbered frames
        gt/gt.txt        frame,id,bb_left,bb_top,bb_width,bb_height,conf,class,vis

The GT class of interest is pedestrian (class 1). MOT's other classes are people
on vehicles, static people, distractors and vehicles that the benchmark treats
as "ignore" for pedestrian tracking; conflating them into the person score would
misrepresent both precision and recall. This adapter scores against class-1
pedestrians above a visibility floor and records the simplification plainly, the
way every honest number in this project is qualified.
"""
from __future__ import annotations

import configparser
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import cv2

from prahari.common.models import ObjectClass
from prahari.edge.sources.base import Frame, VideoSource

# MOT class ids. Only pedestrians (1) are scored as persons; the rest are the
# benchmark's ignore/distractor set for pedestrian tracking.
MOT_PEDESTRIAN = 1
MIN_VISIBILITY = 0.25


def load_gt(gt_path: Path, min_visibility: float = MIN_VISIBILITY
            ) -> dict[int, list[dict[str, Any]]]:
    """Parse a MOT gt.txt into per-frame ground-truth boxes.

    Returns {frame_index (1-based): [ {object_class, bbox:[x1,y1,x2,y2], track_id} ]}.
    Only active (conf != 0) class-1 pedestrians above the visibility floor are
    kept, matching how the MOT benchmark scores pedestrian detection.
    """
    by_frame: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not gt_path.exists():
        return by_frame

    for line in gt_path.read_text().splitlines():
        parts = line.strip().split(",")
        if len(parts) < 6:
            continue
        try:
            frame = int(parts[0])
            tid = int(parts[1])
            x, y, w, h = (float(parts[2]), float(parts[3]),
                          float(parts[4]), float(parts[5]))
            conf = float(parts[6]) if len(parts) > 6 else 1.0
            cls = int(float(parts[7])) if len(parts) > 7 else MOT_PEDESTRIAN
            vis = float(parts[8]) if len(parts) > 8 else 1.0
        except (ValueError, IndexError):
            continue

        if conf == 0:                       # explicitly inactive / ignore
            continue
        if cls != MOT_PEDESTRIAN:
            continue
        if vis < min_visibility:
            continue

        by_frame[frame].append({
            "object_class": ObjectClass.PERSON.value,
            "bbox": [x, y, x + w, y + h],
            "track_id": tid,
            "label": "gt",
        })
    return dict(by_frame)


class MotSequenceSource(VideoSource):
    """Serves a MOT sequence's frames with their ground truth attached.

    Each Frame carries its GT boxes in `ground_truth`, in the same dict shape the
    simulator uses, so the harness scores real footage through exactly the same
    path it scores the simulator - no special case downstream.
    """

    def __init__(self, sequence_dir: Path, camera_id: str | None = None,
                 min_visibility: float = MIN_VISIBILITY) -> None:
        self.sequence_dir = Path(sequence_dir)
        self.camera_id = camera_id or self.sequence_dir.name
        self.min_visibility = min_visibility

        self._info = self._read_seqinfo()
        self.width = int(self._info.get("imWidth", 0))
        self.height = int(self._info.get("imHeight", 0))
        self.nominal_fps = float(self._info.get("frameRate", 25) or 25)
        self._seq_length = int(self._info.get("seqLength", 0))
        self._img_dir = self.sequence_dir / self._info.get("imDir", "img1")
        self._img_ext = self._info.get("imExt", ".jpg")

        self._frames: list[Path] = []
        self._gt: dict[int, list[dict[str, Any]]] = {}
        self._pos = 0
        self._t0 = datetime.now(timezone.utc)

    def _read_seqinfo(self) -> dict[str, Any]:
        ini = self.sequence_dir / "seqinfo.ini"
        if not ini.exists():
            return {}
        parser = configparser.ConfigParser()
        # Preserve key case. configparser lowercases option names by default,
        # which turns MOT's imWidth/imHeight/seqLength into imwidth/... and every
        # camelCase lookup then silently returns None - the sequence loads at
        # 0x0 with a defaulted length. MOT uses camelCase keys, so keep them.
        parser.optionxform = str
        parser.read(ini)
        if parser.has_section("Sequence"):
            return dict(parser["Sequence"])
        return {}

    def open(self) -> bool:
        if not self._img_dir.exists():
            return False
        self._frames = sorted(self._img_dir.glob(f"*{self._img_ext}"))
        if not self._frames:
            # Fall back to any common image extension.
            for ext in (".jpg", ".png", ".jpeg"):
                self._frames = sorted(self._img_dir.glob(f"*{ext}"))
                if self._frames:
                    self._img_ext = ext
                    break
        if not self._frames:
            return False
        self._gt = load_gt(self.sequence_dir / "gt" / "gt.txt", self.min_visibility)
        if self.width == 0 or self.height == 0:
            probe = cv2.imread(str(self._frames[0]))
            if probe is not None:
                self.height, self.width = probe.shape[:2]
        self._pos = 0
        return True

    def read(self) -> Frame | None:
        if self._pos >= len(self._frames):
            return None
        path = self._frames[self._pos]
        image = cv2.imread(str(path))
        self._pos += 1
        if image is None:
            return None

        # MOT frame numbers are 1-based and derived from the filename, so GT
        # lines up even if a frame failed to decode and was skipped.
        try:
            frame_no = int(path.stem)
        except ValueError:
            frame_no = self._pos

        ts = self._t0 + timedelta(seconds=(self._pos - 1) / self.nominal_fps)
        return Frame(
            image=image,
            index=self._pos - 1,
            timestamp=ts,
            ground_truth=self._gt.get(frame_no, []),
        )

    def close(self) -> None:
        self._frames = []

    @property
    def is_simulated(self) -> bool:
        return False

    def describe(self) -> dict[str, Any]:
        d = super().describe()
        d.update({
            "simulated": False,
            "kind": "mot-sequence",
            "sequence": self.sequence_dir.name,
            "frames_available": len(self._frames),
            "gt_frames": len(self._gt),
            "note": "Real footage with real ground truth (MOT format). GT is "
                    "class-1 pedestrians above the visibility floor; MOT's other "
                    "classes are the benchmark's ignore set for pedestrians.",
        })
        return d


def discover_sequences(root: Path) -> list[Path]:
    """Find MOT sequence directories under a root (a dataset or a single seq)."""
    root = Path(root)
    if (root / "seqinfo.ini").exists() or (root / "img1").exists():
        return [root]
    out = []
    for p in sorted(root.rglob("seqinfo.ini")):
        out.append(p.parent)
    if not out:
        for p in sorted(root.rglob("img1")):
            if p.is_dir():
                out.append(p.parent)
    return out
