"""Evidence capture and a tamper-evident event ledger.

Two jobs.

**Capture.** An alert without evidence is an assertion. Prahari keeps a rolling
buffer of recent frames per camera so that when a rule fires it can produce a
clip covering the seconds *before* the trigger as well as after - which is the
part an operator actually needs, because by the time a tripwire fires the
interesting approach has already happened.

The buffer holds JPEG-encoded frames rather than raw arrays. Raw 720p frames
cost ~2.7 MB each, so a 3-second pre-roll across a dozen cameras would consume
over a gigabyte of RAM on a node that may only have four. Encoded, the same
buffer costs a few megabytes per camera and the decode cost is paid once, on the
rare frames that become evidence.

**Integrity.** Each event is appended to a hash chain: every entry commits to the
hash of the one before it, so altering or removing any event after the fact
breaks every subsequent link. This matters specifically because of the offline
story - an edge node that has been disconnected for three days is asking the
sector core to accept a backlog of events on trust, and the chain is what turns
that into something checkable. It also means an evidence package carries an
integrity statement if it is ever put in front of anyone who has to rely on it.

A caveat stated plainly: the edge chain *alone* is tamper-*evident*, not
tamper-*proof* — a party holding the node's key can re-stamp the whole chain into
a consistent rewrite. That gap is closed at the sector core: ``core/notary.py``
countersigns each node's chain head with a key no node holds and remembers the
hash it witnessed, so witnessed history cannot be rewritten undetected (see
docs/security.md, "Evidence integrity"). The remaining steps — hardware-backed
keys and external anchoring of the checkpoint head — are designed for there and
not built.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from prahari.common.db import Database
from prahari.common.models import Event, EvidenceRef

log = logging.getLogger("prahari.evidence")

JPEG_QUALITY = 82


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_bytes(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


# =====================================================================
# Rolling capture buffer
# =====================================================================

class EvidenceBuffer:
    """Per-camera ring buffer of recent frames, JPEG-encoded."""

    def __init__(self, fps: float, before_seconds: float, after_seconds: float) -> None:
        self.fps = max(1.0, fps)
        self.before_seconds = before_seconds
        self.after_seconds = after_seconds
        maxlen = max(4, int(self.fps * before_seconds) + 2)
        self._frames: deque[tuple[float, bytes]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def push(self, image: np.ndarray, timestamp_s: float) -> None:
        ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
        if not ok:
            return
        with self._lock:
            self._frames.append((timestamp_s, buf.tobytes()))

    def snapshot(self) -> list[tuple[float, bytes]]:
        with self._lock:
            return list(self._frames)

    def latest(self) -> tuple[float, bytes] | None:
        with self._lock:
            return self._frames[-1] if self._frames else None


class PendingClip:
    """An event whose post-roll is still being collected."""

    def __init__(self, event_id: str, pre_roll: list[tuple[float, bytes]],
                 trigger_ts: float, after_seconds: float, fps: float) -> None:
        self.event_id = event_id
        self.frames = list(pre_roll)
        self.trigger_ts = trigger_ts
        self.after_seconds = after_seconds
        self.fps = fps

    def push(self, timestamp_s: float, jpeg: bytes) -> None:
        self.frames.append((timestamp_s, jpeg))

    @property
    def complete(self) -> bool:
        if not self.frames:
            return True
        return (self.frames[-1][0] - self.trigger_ts) >= self.after_seconds


# =====================================================================
# Evidence store
# =====================================================================

class EvidenceStore:
    """Writes evidence packages to disk under a date/camera/event hierarchy."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _dir_for(self, camera_id: str, event_id: str, when: datetime) -> Path:
        import re
        c = re.sub(r'[^a-zA-Z0-9_\-]', '', camera_id)
        e = re.sub(r'[^a-zA-Z0-9_\-]', '', event_id)
        d = (self.root / when.strftime("%Y") / when.strftime("%m") / when.strftime("%d")
             / c / e)
        d.mkdir(parents=True, exist_ok=True)
        return d

    def write_frame(self, camera_id: str, event_id: str, when: datetime,
                    jpeg: bytes) -> tuple[Path, Path, str]:
        """Persist the trigger frame and a small thumbnail.

        The thumbnail exists for the bandwidth story: it is what gets pushed to
        the sector core on a constrained link, with the full clip left at the
        edge until somebody asks for it.
        """
        d = self._dir_for(camera_id, event_id, when)
        frame_path = d / "frame.jpg"
        frame_path.write_bytes(jpeg)

        thumb_path = d / "thumb.jpg"
        arr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if arr is not None:
            h, w = arr.shape[:2]
            scale = 240.0 / max(1, w)
            thumb = cv2.resize(arr, (240, max(1, int(h * scale))),
                               interpolation=cv2.INTER_AREA)
            cv2.imwrite(str(thumb_path), thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 70])
        return frame_path, thumb_path, sha256_bytes(jpeg)

    def write_mask(self, camera_id: str, event_id: str, when: datetime,
                   frame_jpeg: bytes, seg) -> tuple[Path | None, str]:
        """Render the segmentation as an overlay on the trigger frame.

        The operator gets the object outlined precisely on the actual frame -
        far easier to verify than a rectangle - plus the mask itself. Kept small
        (a PNG overlay), so it is cheap to store and to ship as evidence.
        """
        d = self._dir_for(camera_id, event_id, when)
        arr = cv2.imdecode(np.frombuffer(frame_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return None, ""
        overlay = arr.copy()
        ox, oy = seg.offset
        mh, mw = seg.mask.shape[:2]
        region = overlay[oy:oy + mh, ox:ox + mw]
        if region.shape[:2] == seg.mask.shape[:2]:
            tint = np.zeros_like(region)
            tint[:, :] = (64, 196, 255)
            m = seg.mask.astype(bool)
            region[m] = (0.5 * region[m] + 0.5 * tint[m]).astype(np.uint8)
        if len(seg.polygon) >= 3:
            pts = np.array([[int(p.x), int(p.y)] for p in seg.polygon], dtype=np.int32)
            cv2.polylines(overlay, [pts], True, (64, 196, 255), 2, cv2.LINE_AA)
        gx, gy = seg.ground_contact
        cv2.circle(overlay, (int(gx), int(gy)), 4, (0, 0, 255), -1)

        mask_path = d / "mask.png"
        cv2.imwrite(str(mask_path), overlay)
        return mask_path, sha256_file(mask_path)

    def prune(self, days: int) -> int:
        """Delete evidence older than the given number of days.
        
        Border outposts have finite storage, and raw video evidence consumes it quickly.
        """
        import shutil
        from datetime import datetime, timezone, timedelta
        
        cutoff = datetime.now(timezone.utc) - timedelta(days=days)
        deleted = 0
        
        # Traverse YYYY/MM/DD structure
        if not self.root.exists():
            return 0
            
        for year_dir in self.root.iterdir():
            if not year_dir.is_dir() or not year_dir.name.isdigit():
                continue
            for month_dir in year_dir.iterdir():
                if not month_dir.is_dir() or not month_dir.name.isdigit():
                    continue
                for day_dir in month_dir.iterdir():
                    if not day_dir.is_dir() or not day_dir.name.isdigit():
                        continue
                        
                    try:
                        dir_date = datetime(
                            int(year_dir.name),
                            int(month_dir.name),
                            int(day_dir.name),
                            tzinfo=timezone.utc
                        )
                    except ValueError:
                        continue
                        
                    if dir_date < cutoff:
                        shutil.rmtree(day_dir, ignore_errors=True)
                        deleted += 1
                        
                # Cleanup empty month/year dirs
                if not any(month_dir.iterdir()):
                    month_dir.rmdir()
            if not any(year_dir.iterdir()):
                year_dir.rmdir()
                
        return deleted

    def write_clip(self, camera_id: str, event_id: str, when: datetime,
                   frames: list[tuple[float, bytes]], fps: float) -> tuple[Path | None, str, float, int]:
        if not frames:
            return None, "", 0.0, 0
        d = self._dir_for(camera_id, event_id, when)
        clip_path = d / "clip.mp4"

        first = cv2.imdecode(np.frombuffer(frames[0][1], dtype=np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            return None, "", 0.0, 0
        h, w = first.shape[:2]

        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(str(clip_path), fourcc, max(1.0, fps), (w, h))
        if not writer.isOpened():
            log.error("could not open video writer for %s", clip_path)
            return None, "", 0.0, 0
        try:
            for _, jpeg in frames:
                arr = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
                if arr is None:
                    continue
                if arr.shape[:2] != (h, w):
                    arr = cv2.resize(arr, (w, h))
                writer.write(arr)
        finally:
            writer.release()

        duration = frames[-1][0] - frames[0][0]
        size = clip_path.stat().st_size if clip_path.exists() else 0
        return clip_path, sha256_file(clip_path), round(duration, 2), size

    def evict_clip(self, ref: EvidenceRef) -> int:
        """Delete a clip but keep its frame, thumbnail and hashes.

        The disk-pressure policy in one method: under storage exhaustion Prahari
        degrades evidence, never the record. An event with no clip is weakened;
        an event that disappeared is a hole in the account of the night, and no
        amount of disk pressure justifies creating one.
        """
        freed = 0
        if ref.clip_path:
            p = Path(ref.clip_path)
            if p.exists():
                freed = p.stat().st_size
                p.unlink()
        return freed

    def purge_older_than(self, days: int) -> int:
        """Retention. Returns bytes reclaimed."""
        cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
        freed = 0
        for path in self.root.rglob("*"):
            if path.is_file() and path.stat().st_mtime < cutoff:
                freed += path.stat().st_size
                try:
                    path.unlink()
                except OSError:
                    log.warning("could not purge %s", path)
        return freed


# =====================================================================
# Hash chain
# =====================================================================

def compute_entry_hash(index: int, prev_hash: str, event: Event) -> str:
    """Deterministic commitment to an event and its evidence.

    Only fields that must not change after the fact are committed to. Mutable
    operational state - acknowledgement, sync status, operator feedback - is
    deliberately excluded, so an operator acknowledging an alert does not break
    the chain.
    """
    ev = event.evidence
    body = {
        "i": index,
        "prev": prev_hash,
        "event_id": event.event_id,
        "node": event.node_id,
        "camera": event.camera_id,
        "type": event.event_type.value,
        "ts": event.timestamp.astimezone(timezone.utc).isoformat(),
        "mono_ns": event.monotonic_ns,
        "object": event.object_class.value,
        "track": event.track_id,
        "zone": event.zone_id,
        "score": round(event.priority_score, 4),
        "frame_sha256": ev.frame_sha256 if ev else None,
        "clip_sha256": ev.clip_sha256 if ev else None,
    }
    # A decision not to interrupt a human is exactly the kind of thing that must
    # not be editable afterwards, so the patrol determination is committed to as
    # well. Added conditionally: an event with no patrol assessment hashes
    # exactly as it did before this field existed, so chains written by earlier
    # versions keep verifying after an upgrade.
    if event.patrol is not None:
        body["patrol"] = [
            event.patrol.decision.value,
            event.patrol.patrol_id,
            round(event.patrol.match_score, 4),
        ]
    blob = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


class EvidenceLedger:
    """Append-only chain over the event log, persisted in the events table."""

    def __init__(self, db: Database) -> None:
        self.db = db
        self._lock = threading.Lock()

    def append(self, event: Event) -> Event:
        """Stamp an event with its ledger position and hash.

        Appending is strictly once per event. An earlier version re-appended when
        the evidence clip finished, to commit to the clip hash - which handed the
        event a second, higher index while leaving its original position orphaned
        and broke every link after it. An append-only log cannot have entries
        rewritten; if the clip hash is to be committed to, the append must wait
        until the clip exists. See `EvidenceLedger.is_chained`.
        """
        with self._lock:
            if event.ledger_index:
                return event          # already chained; never re-index
            last_index, last_hash = self.db.last_ledger_entry()
            index = last_index + 1
            event.ledger_index = index
            event.prev_hash = last_hash
            event.entry_hash = compute_entry_hash(index, last_hash, event)
            return event

    @staticmethod
    def is_chained(event: Event) -> bool:
        return bool(event.ledger_index)

    def verify(self) -> dict[str, Any]:
        """Walk the whole chain and report the first break, if any."""
        entries = self.db.ledger_entries()
        if not entries:
            return {"valid": True, "entries": 0, "message": "ledger is empty"}

        prev_hash = ""
        for index, stored_prev, stored_hash, event_id in entries:
            if stored_prev != prev_hash:
                return {
                    "valid": False, "entries": len(entries), "broken_at": index,
                    "event_id": event_id,
                    "message": (
                        f"link broken at entry {index}: recorded predecessor hash does "
                        f"not match the actual previous entry"
                    ),
                }
            event = self.db.get_event(event_id)
            if event is None:
                return {
                    "valid": False, "entries": len(entries), "broken_at": index,
                    "event_id": event_id,
                    "message": f"entry {index} references a deleted event",
                }
            recomputed = compute_entry_hash(index, stored_prev, event)
            if recomputed != stored_hash:
                return {
                    "valid": False, "entries": len(entries), "broken_at": index,
                    "event_id": event_id,
                    "message": (
                        f"entry {index} has been altered since it was written: the "
                        f"recomputed hash does not match the recorded one"
                    ),
                }
            prev_hash = stored_hash

        return {
            "valid": True, "entries": len(entries), "head": prev_hash,
            "message": f"all {len(entries)} entries verify against the chain",
        }
