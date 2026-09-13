"""In-process publish/subscribe bus with NATS-style subjects.

The original design called for NATS JetStream. At edge scale (one node, a dozen
camera pipelines) a broker is a process to install, supervise and fail, for no
gain. So the bus is in-process - but the *subject naming and message schema are
identical* to what a JetStream deployment would use, so swapping the transport
is a change to this file alone. Nothing upstream knows the difference.

Subjects:
    prahari.<node>.detections.<camera_id>
    prahari.<node>.tracks.<camera_id>
    prahari.<node>.events.<camera_id>
    prahari.<node>.camera.health.<camera_id>
    prahari.<node>.sync.status
    prahari.<node>.system.status
"""
from __future__ import annotations

import asyncio
import fnmatch
import logging
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger("prahari.bus")

Handler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class MessageBus:
    def __init__(self) -> None:
        self._subs: dict[str, list[Handler]] = defaultdict(list)
        self._queues: list[tuple[str, asyncio.Queue]] = []
        self._lock = asyncio.Lock()

    # -- subscription ---------------------------------------------------
    def subscribe(self, pattern: str, handler: Handler) -> None:
        """Subscribe with a glob pattern, e.g. 'prahari.*.events.*'."""
        self._subs[pattern].append(handler)

    def unsubscribe(self, pattern: str, handler: Handler) -> None:
        if handler in self._subs.get(pattern, []):
            self._subs[pattern].remove(handler)

    def queue(self, pattern: str, maxsize: int = 256) -> asyncio.Queue:
        """A backpressure-aware queue subscription.

        Used by the WebSocket fan-out. When a slow browser cannot keep up we drop
        the oldest message rather than stalling the detection pipeline - a laggy
        UI must never be able to slow down event detection.
        """
        q: asyncio.Queue = asyncio.Queue(maxsize=maxsize)
        self._queues.append((pattern, q))
        return q

    def release_queue(self, q: asyncio.Queue) -> None:
        self._queues = [(p, qq) for (p, qq) in self._queues if qq is not q]

    # -- publication ----------------------------------------------------
    async def publish(self, subject: str, payload: dict[str, Any]) -> None:
        for pattern, handlers in list(self._subs.items()):
            if fnmatch.fnmatch(subject, pattern):
                for h in list(handlers):
                    try:
                        res = h(subject, payload)
                        if asyncio.iscoroutine(res):
                            await res
                    except Exception:
                        log.exception("bus handler failed for %s", subject)

        for pattern, q in list(self._queues):
            if not fnmatch.fnmatch(subject, pattern):
                continue
            msg = {"subject": subject, "data": payload}
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                # Drop oldest, keep newest: operators care about what is
                # happening now, not what a stalled socket missed.
                try:
                    q.get_nowait()
                    q.put_nowait(msg)
                except Exception:
                    pass

    def publish_soon(self, subject: str, payload: dict[str, Any]) -> None:
        """Fire-and-forget publish from synchronous pipeline code."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self.publish(subject, payload))


_bus: MessageBus | None = None


def get_bus() -> MessageBus:
    global _bus
    if _bus is None:
        _bus = MessageBus()
    return _bus


def subj(node_id: str, kind: str, camera_id: str | None = None) -> str:
    base = f"prahari.{node_id}.{kind}"
    return f"{base}.{camera_id}" if camera_id else base
