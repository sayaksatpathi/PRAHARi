# Screenshot Capture Spec

Capture these from a **live run** (see [`../demo/demo-checklist.md`](../demo/demo-checklist.md)).
The `.png` files are not committed — this spec is the source of truth for what
each shot must show. Capture at projector resolution, no personal/system
notifications visible.

| File | Screen | Must show |
|------|--------|-----------|
| `login.png` | Sign-in | Clean auth screen; no credentials visible |
| `live-camera.png` | Live camera view | A certified camera with live detections + tracks overlaid |
| `alerts.png` | Alerts list | Governed alerts; suppression count visible (rationed alerting) |
| `cross-camera.png` | Cross-camera Re-ID | Same person/track matched across two cameras |
| `patrol-deviation.png` | Patrol Deviation | A matched patrol suppressed **and** a flagged deviation, side by side |
| `camera-capability.png` | Camera Capability | A Camera Capability Certificate with measured resolution/FPS and DORI bands |
| `evidence-integrity.png` | Evidence detail | Clip + frame + metadata + intact hash-chain state |
| `system-health.png` | System Health | Edge/core status; queue depth during and after a connectivity cut |

## Tips

- For `system-health.png`, capture **twice** — during the network cut (queue
  growing) and after restore (queue drained) — to tell the store-and-forward story.
- Prefer the open-border camera (e.g. CAM-022) for `alerts.png` to show
  suppression of lawful traffic.
- Keep timestamps consistent across shots if you'll present them as one incident.
