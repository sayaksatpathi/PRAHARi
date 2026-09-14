# Where Prahari sits: the video-intelligence layer

> **Prahari is the video-intelligence layer within a multi-sensor
> border-surveillance ecosystem.**

Not the whole system. That positioning is deliberate, and it is a stronger claim
than "complete border security solution", because it is one this build can
actually support.

```
                          PRAHARI
                             │
              ┌──────────────┼──────────────┐
              ↓              ↓              ↓
            CCTV           Radar          PIDS
        (implemented)   (integration   (integration
                            hook)          hook)
              │              │              │
              └──────────────┼──────────────┘
                             ↓
                  Fusion / Correlation API
                             ↓
                    Border Intelligence
```

**Implemented today: the CCTV branch, end to end.** Radar and PIDS are
*integration hooks* — a defined place to attach them and a defined contract for
what they would exchange. They are not implemented, and nothing in this
repository should be read as claiming otherwise.

## Why this is the right position rather than a retreat

A border sector already has, or will have, sensors that video cannot beat at
their own job. Radar sees through fog and dark at ranges no camera reaches.
Buried seismic and fibre-optic PIDS detect a fence breach or footfall with no
line of sight and no illumination. Presenting a video system as a replacement for
those invites exactly one question, and it is the one that ends the
conversation.

What video uniquely provides is **identification and context**. Radar reports a
track: bearing, range, speed. It cannot say whether that track is a smuggler, a
patrol, a villager on a treaty-open crossing, or cattle — and at an open border
that distinction *is* the problem. Prahari's whole design is about that
distinction: measured camera capability, learned pattern of life, patrol
conformance, cross-camera identity, sealed evidence.

So the honest architecture is complementary, not competitive:

| Sensor | Strength | Blind to |
|---|---|---|
| Radar | Range, fog, darkness, all-weather | What the object *is* |
| PIDS / seismic / fibre | No line of sight, buried, hard to defeat | What, who, and why |
| **Video (Prahari)** | **Classification, identity, intent, evidence** | **Fog, darkness beyond IR, occlusion** |

A cue from any of them is worth more when the others corroborate it. That is the
argument for fusion, and it is why the hook exists even though the fusion does
not.

## The integration hook

Prahari already has the internal shape a fusion layer needs, because cross-camera
correlation solves the same problem one level down — correlating observations of
one object across independent sources with imperfect timing.

**Inbound — an external sensor cue.** A radar or PIDS detection arrives as a cue
that raises the priority of, or corroborates, video events in a matching place
and time window. The existing `PriorityFactor` mechanism carries it: the operator
sees "radar corroboration +0.15" in the same visible arithmetic as every other
contribution, rather than an opaque score change.

**Outbound — a Prahari event.** Events already serialise to a transport-agnostic
JSON schema and already travel over an in-process bus with NATS-shaped subjects,
a REST API, a WebSocket feed and a store-and-forward sync queue. Publishing the
same records to MQTT or a sector fusion bus is a transport addition, not an
architectural change.

**What would have to be built.** Spatial registration between sensor frames
(radar bearing/range to camera image coordinates needs a surveyed transform),
time synchronisation across sensors with independent clocks, and an association
policy with an explicit false-corroboration cost. None of that is trivial, and
listing it is the point: the hook is a place to attach, not a claim that
attaching is easy.

## What this means for the claim

Accurate: *"Prahari is the video-intelligence layer of a multi-sensor system. The
video layer is implemented and measured. Integration with radar and PIDS is a
defined extension point, not a delivered capability."*

Not accurate, and should not be said: that Prahari performs sensor fusion today,
or that radar/PIDS integration has been demonstrated.

See [architecture.md](architecture.md) for the implemented layers and
[limitations.md](limitations.md) for the full bounded-capability statement.
