"""Serve the demo footage as RTSP and report what Prahari should point at.

Starts MediaMTX with a generated configuration, one RTSP path per recording,
then stays in the foreground until interrupted. Streams start on demand: ffmpeg
is launched only when something connects.

    .venv/Scripts/python.exe scripts/serve_rtsp.py
    .venv/Scripts/python.exe scripts/serve_rtsp.py --check     # preflight only

Then point a camera at one of the printed URLs, or run:

    .venv/Scripts/python.exe scripts/use_rtsp_cameras.py

which switches the demo fleet from simulated sources to those RTSP endpoints.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from prahari.edge.rtsp_serve import MediaMtxServer  # noqa: E402

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s  %(levelname)-7s %(name)-18s %(message)s",
                    datefmt="%H:%M:%S")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footage", type=Path, default=Path("footage"))
    parser.add_argument("--port", type=int, default=8554)
    parser.add_argument("--api-port", type=int, default=9997)
    parser.add_argument("--check", action="store_true",
                        help="run preflight checks and exit")
    parser.add_argument("--no-transcode", action="store_true",
                        help="stream-copy instead of transcoding to H.264 "
                             "(only if the recordings are already H.264)")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    server = MediaMtxServer(root, args.footage, args.port, args.api_port)

    problems = server.preflight()
    if problems:
        print("Preflight failed:\n")
        for p in problems:
            print(f"  - {p}")
        return 1

    print(f"MediaMTX   {server.exe}")
    print(f"ffmpeg     {server.ffmpeg}")
    print(f"footage    {len(server.footage())} recording(s) in {args.footage}/\n")
    if args.check:
        print("Preflight OK.")
        return 0

    if not server.start(transcode=not args.no_transcode):
        print("\nMediaMTX failed to start. See the log above.")
        return 1

    print("\n  RTSP endpoints, live now:\n")
    for name in sorted(server.footage()):
        print(f"    {name:<12} {server.url_for(name)}")
    print("\n  These are real RTSP streams. Prahari ingests them through the")
    print("  same StreamSource that reads a camera on a pole.")
    print("\n  Point the demo fleet at them:")
    print("    .venv/Scripts/python.exe scripts/use_rtsp_cameras.py")
    print("\n  Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
