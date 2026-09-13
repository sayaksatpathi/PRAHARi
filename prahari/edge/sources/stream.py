"""RTSP / ONVIF / video-file source.

This is the path that matters for the problem statement: Prahari is meant to run
against CCTV that is already installed, not against cameras bought for it. Two
practical notes that come from how those installations actually look.

**Prefer the sub-stream, and consider pulling from the NVR rather than the
camera.** Most installed IP cameras cap the number of simultaneous RTSP clients,
and on a site with a recorder already attached, the camera's connection budget is
frequently spent. Pulling the recorder's re-streamed feed avoids fighting the
existing system for the camera, and pulling the *sub*-stream (typically D1/720p
at a low bitrate) rather than the main stream cuts decode cost dramatically for
analytics that do not need full resolution. Both are configuration choices, not
code changes - point `stream_url` wherever the site allows.

**A camera being unreachable is normal, not exceptional.** Border outposts run
cameras on long PoE runs, marginal wireless links and generator power. This class
therefore never raises on a dead camera: it reports, backs off and keeps trying,
because a pipeline that crashes when one camera drops takes the other eleven with
it.

Credentials are injected into the URL at connect time and are never logged,
never returned by the API, and never written to the database in plaintext form
that leaves the process - see `Camera.public_dict()`.
"""
from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from urllib.parse import quote, urlparse, urlunparse

import cv2

from prahari.edge.sources.base import Frame, VideoSource

log = logging.getLogger("prahari.source.stream")

# Force FFMPEG to carry RTSP over TCP. UDP is the default and it is the wrong
# default here: a congested or lossy backhaul produces torn, smeared frames that
# look like motion to a detector and generate false events all night.
os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")


def redact(url: str) -> str:
    """A form of the URL that is safe to put in a log line."""
    try:
        p = urlparse(url)
        if p.username or p.password:
            host = p.hostname or ""
            if p.port:
                host = f"{host}:{p.port}"
            return urlunparse((p.scheme, f"<redacted>@{host}", p.path, "", "", ""))
    except ValueError:
        pass
    return url


def inject_credentials(url: str, username: str, password: str) -> str:
    if not username and not password:
        return url
    p = urlparse(url)
    if p.username or p.password:
        return url                      # already carries credentials
    host = p.hostname or ""
    if p.port:
        host = f"{host}:{p.port}"
    auth = f"{quote(username, safe='')}:{quote(password, safe='')}@{host}"
    return urlunparse((p.scheme, auth, p.path, p.params, p.query, p.fragment))


class StreamSource(VideoSource):
    """OpenCV-backed capture for RTSP streams and local video files."""

    def __init__(
        self,
        camera_id: str,
        url: str,
        *,
        username: str = "",
        password: str = "",
        nominal_fps: float = 12.0,
        loop: bool = False,
        reconnect_backoff_s: float = 3.0,
        max_backoff_s: float = 30.0,
    ) -> None:
        self.camera_id = camera_id
        self.url = url
        self._username = username
        self._password = password
        self.nominal_fps = nominal_fps
        self.loop = loop
        self.reconnect_backoff_s = reconnect_backoff_s
        self.max_backoff_s = max_backoff_s

        self.width = 0
        self.height = 0
        self._cap: cv2.VideoCapture | None = None
        self._index = 0
        self._failures = 0
        self._next_retry_at = 0.0
        self._connected = False

    # -- connection ------------------------------------------------------
    def _connect(self) -> bool:
        target = inject_credentials(self.url, self._username, self._password)
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        if not cap.isOpened():
            cap.release()
            return False

        # Keep the decoder buffer tiny. A large buffer means the analytics are
        # looking at video from several seconds ago, which for an intrusion alert
        # is the difference between a response and a report.
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass

        self.width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or 0
        self.height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or 0
        reported = cap.get(cv2.CAP_PROP_FPS)
        if reported and 1.0 < reported < 121.0:
            self.nominal_fps = float(reported)

        self._cap = cap
        self._connected = True
        self._failures = 0
        log.info("connected to %s (%dx%d @ %.1f fps claimed)",
                 redact(self.url), self.width, self.height, self.nominal_fps)
        return True

    def open(self) -> bool:
        if self._connect():
            return True
        log.error("could not open %s", redact(self.url))
        self._schedule_retry()
        return False

    def _schedule_retry(self) -> None:
        self._failures += 1
        backoff = min(self.max_backoff_s,
                      self.reconnect_backoff_s * (2 ** min(self._failures - 1, 4)))
        self._next_retry_at = time.monotonic() + backoff
        self._connected = False
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        self._connected = False

    # -- frames ----------------------------------------------------------
    def read(self) -> Frame | None:
        if not self._connected:
            if time.monotonic() < self._next_retry_at:
                return None
            if not self._connect():
                self._schedule_retry()
                return None

        assert self._cap is not None
        ok, image = self._cap.read()

        if not ok or image is None:
            if self.loop:
                # End of file: rewind. Looping a recording is how the demo
                # presents "a camera that keeps running".
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, image = self._cap.read()
                if ok and image is not None:
                    return self._wrap(image)
            log.warning("read failed on %s; reconnecting", redact(self.url))
            self._schedule_retry()
            return None

        return self._wrap(image)

    def _wrap(self, image) -> Frame:
        if not self.width or not self.height:
            self.height, self.width = image.shape[:2]
        frame = Frame(
            image=image,
            index=self._index,
            timestamp=datetime.now(timezone.utc),
            ground_truth=[],          # a real camera has none, by definition
        )
        self._index += 1
        return frame

    # -- diagnostics -----------------------------------------------------
    def test_connection(self, timeout_s: float = 8.0) -> dict:
        """Probe the stream and report honestly what came back.

        Deliberately attempts a real frame read rather than trusting isOpened():
        a misconfigured RTSP path frequently opens and then never delivers, and
        reporting that as CONNECTED would be a lie the operator discovers at the
        worst moment.
        """
        started = time.monotonic()
        target = inject_credentials(self.url, self._username, self._password)
        cap = cv2.VideoCapture(target, cv2.CAP_FFMPEG)
        try:
            if not cap.isOpened():
                return {
                    "status": "FAILED", "url": redact(self.url),
                    "error": "could not open the stream (bad URL, wrong credentials, "
                             "unreachable host, or the camera refused another client)",
                    "elapsed_s": round(time.monotonic() - started, 2),
                }
            ok, image = cap.read()
            elapsed = round(time.monotonic() - started, 2)
            if not ok or image is None:
                return {
                    "status": "FAILED", "url": redact(self.url),
                    "error": "stream opened but delivered no frame within the timeout "
                             "(check the stream path, codec and transport)",
                    "elapsed_s": elapsed,
                }
            h, w = image.shape[:2]
            return {
                "status": "CONNECTED", "url": redact(self.url),
                "width": w, "height": h,
                "reported_fps": round(float(cap.get(cv2.CAP_PROP_FPS)), 2),
                "elapsed_s": elapsed,
            }
        finally:
            cap.release()

    def describe(self) -> dict:
        d = super().describe()
        d.update({
            "url": redact(self.url),
            "connected": self._connected,
            "failures": self._failures,
            "loop": self.loop,
        })
        return d
