"""Repoint the demo fleet from simulated sources to real RTSP endpoints.

Switches cameras on a running node to `source_kind: rtsp`, pointing at the
MediaMTX paths from `scripts/serve_rtsp.py`. Nothing below the source layer
changes: the same profiling, detection, tracking, rules, evidence and sync run
against frames that now arrive over the wire.

    scripts/serve_rtsp.py                          # terminal 1
    scripts/use_rtsp_cameras.py                    # terminal 2
    scripts/use_rtsp_cameras.py --only CAM-011,CAM-014
    scripts/use_rtsp_cameras.py --revert           # back to simulated

Switching a camera makes the node re-open its source and re-profile it, because
a stream's measured properties are not assumed to carry over from the simulated
one it replaces - which is the whole point of measuring them.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8420"


def login(client: httpx.Client) -> None:
    pw_file = ROOT / "var" / ".demo_admin_pw"
    if not pw_file.exists():
        raise SystemExit(
            "No admin password found at var/.demo_admin_pw.\n"
            "Start the node with scripts/run_demo.sh, which saves it."
        )
    r = client.post("/api/auth/login",
                    json={"username": "admin",
                          "password": pw_file.read_text().strip()})
    r.raise_for_status()
    client.headers["Authorization"] = f"Bearer {r.json()['token']}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--rtsp-port", type=int, default=8554)
    parser.add_argument("--only", default="",
                        help="comma-separated camera ids; default is every "
                             "camera with a matching recording")
    parser.add_argument("--revert", action="store_true",
                        help="switch back to simulated sources")
    parser.add_argument("--footage", type=Path, default=Path("footage"))
    args = parser.parse_args()

    wanted = {c.strip().upper() for c in args.only.split(",") if c.strip()}
    available = {p.stem.upper() for p in (ROOT / args.footage).glob("*.mp4")}

    client = httpx.Client(base_url=args.base, timeout=30.0)
    login(client)

    cameras = client.get("/api/cameras").json()["cameras"]
    changed = []

    for entry in cameras:
        cid = entry["camera_id"]
        if wanted and cid not in wanted:
            continue
        if not args.revert and cid.upper() not in available:
            continue

        full = client.get(f"/api/cameras/{cid}").json()
        # public_dict() strips credentials and redacts the URL, so rebuild the
        # payload from the fields the API will accept rather than round-tripping
        # a redacted value back into storage.
        payload = {k: v for k, v in full.items()
                   if k not in ("runtime", "certificate", "zones",
                                "has_credentials")}
        payload.pop("stream_url", None)

        if args.revert:
            payload["source_kind"] = "simulator"
            payload["stream_url"] = ""
            label = "simulator"
        else:
            payload["source_kind"] = "rtsp"
            payload["stream_url"] = (
                f"rtsp://127.0.0.1:{args.rtsp_port}/{cid.lower()}")
            label = payload["stream_url"]

        r = client.put(f"/api/cameras/{cid}", json=payload)
        if r.status_code != 200:
            print(f"  {cid:<10} FAILED  {r.status_code} {r.text[:120]}")
            continue
        changed.append(cid)
        print(f"  {cid:<10} -> {label}")

    if not changed:
        print("\nNothing changed. Recordings found:",
              ", ".join(sorted(available)) or "(none)")
        print("Generate them with: python scripts/make_footage.py")
        return 1

    print(f"\n{len(changed)} camera(s) switched.")
    if not args.revert:
        print("\nEach will now re-open its source and re-profile from scratch:")
        print("a stream's measured properties are not assumed to carry over")
        print("from the simulated source it replaced. Give it a minute, then")
        print("check Camera Capability in the dashboard.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
