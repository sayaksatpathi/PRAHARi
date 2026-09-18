# Demo Assets

Pointers to the media the live demo uses. The heavy media (`.mp4`) is **not
committed** (gitignored); it is regenerated from the reproducible runners.

## Simulated border-scenario clips (SIMULATED)

Regenerate with `scripts/border_scenarios.py` → `data/demo/border_scenarios/`:

| Clip | Demo moment |
|------|-------------|
| `01_normal_open_border.mp4` | Normal open-border movement → suppressed |
| `02_patrol_matched.mp4` | Patrol on route → suppressed |
| `03_patrol_deviation.mp4` | Patrol deviation → escalated |
| `04_off_route_movement.mp4` | Off-route entrant → alert |
| `05_night_movement.mp4` | Night movement → alert |
| `06_suspicious_activity.mp4` | Group/suspicious → alert |
| `07_camera_tamper.mp4` | Lens covered → tamper alert |
| `08_network_outage.mp4` | Outage → local queue → recovery |

Ground-truth JSONs (`NN_*.json`) are tracked alongside.

## Real-footage detection clips (REAL-FOOTAGE)

Regenerate with `scripts/test_real_clips.py` (Pexels, free-licensed — see
[`../../../DATA_LICENSES.md`](../../../DATA_LICENSES.md)). Ground-level street
footage is the best-matched angle; annotated sample frames land in `var/`.

## Screenshots

Capture specs live in [`../../screenshots/README.md`](../../screenshots/README.md).
Screenshots require the running app and are captured live, not committed here.

> All demo media is **SIMULATED** or **REAL-FOOTAGE (generic scenes)** — never
> real-world border footage or field performance.
