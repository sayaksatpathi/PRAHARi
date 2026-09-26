"""Fetch one real public clip per border case (royalty-free, Mixkit).

Real border CCTV is sensitive/unavailable, so each border *case* is exercised on a
separate real public clip. Clips are royalty-free (Mixkit, no attribution required)
and are NOT committed (gitignored); this script re-fetches them and writes the
manifest that scripts/test_border_cases.py --all consumes.

    python scripts/fetch_border_case_clips.py
    python scripts/test_border_cases.py --all
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data/testing/border_cases"

# case -> Mixkit video id. CCTV-PERSPECTIVE clips only: fixed, elevated/high-angle,
# wide-scene — the way Prahari actually deploys (mounted CCTV, not drones or
# cinematic close-ups). The actual module outputs are reported by
# test_border_cases.py, not assumed here.
CLIPS = {
    "line_crossing":    "4401",    # high-angle junction, people crossing (fixed cam)
    "crowd_group":      "4000",    # high-angle busy city street (people + vehicles)
    "night_movement":   "3428",    # low-light street, people walking at dusk
    "vehicle_traffic":  "3218",    # high-angle street car traffic (fixed cam, daytime)
    "animal_livestock": "10219",   # cattle — person/animal class confusion (not a
                                   # CCTV angle; kept only to test class labelling)
}
URL = "https://assets.mixkit.co/videos/{id}/{id}-{res}.mp4"

# Direct-URL clips (Pexels) for cases needing a specific view. The gate/chokepoint
# clip has close, legible plates — the CAM-011 "Main Gate - Vehicle Lane" case where
# Prahari's certificate actually grants ANPR.
DIRECT = {
    "gate_anpr": ("https://videos.pexels.com/video-files/12405634/"
                  "12405634-hd_1920_1080_30fps.mp4", "pexels 12405634, royalty-free"),
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    for case, (url, src) in DIRECT.items():
        dst = OUT / f"{case}.mp4"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=90) as r:
                data = r.read()
            dst.write_bytes(data)
            manifest.append({"case": case, "path": f"data/testing/border_cases/{case}.mp4",
                             "source": src})
            print(f"{case:18} {src.split(',')[0]} {len(data)//1024}KB")
        except Exception:                        # noqa: BLE001
            print(f"{case:18} FAILED")
    for case, vid in CLIPS.items():
        dst = OUT / f"{case}.mp4"
        got = None
        for res in ("1080", "720", "360"):
            try:
                req = urllib.request.Request(URL.format(id=vid, res=res),
                                             headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=90) as r:
                    data = r.read()
                dst.write_bytes(data)
                got = (res, len(data))
                break
            except Exception:                    # noqa: BLE001
                continue
        if got:
            manifest.append({"case": case, "path": f"data/testing/border_cases/{case}.mp4",
                             "source": f"mixkit.co/{vid} ({got[0]}p), royalty-free"})
            print(f"{case:18} mixkit {vid} {got[0]}p {got[1]//1024}KB")
        else:
            print(f"{case:18} FAILED (all resolutions)")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    print(f"\nwrote {OUT/'manifest.json'} ({len(manifest)} clips)")
    return 0 if manifest else 1


if __name__ == "__main__":
    raise SystemExit(main())
