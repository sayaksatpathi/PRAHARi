# Cross-camera intelligence

The step from "several cameras that each raise their own events" to "a corridor
that knows what should happen next". This is the layer that produces an
intelligence product rather than a stream of independent alarms.

Three things a single camera cannot do:

- **Handoff.** A track leaving one camera and a track arriving at the next are
  linked into one global identity, matched on the learnt transition time, the
  object class, and a coarse appearance signature.
- **Corridor reasoning.** An object that entered the corridor and was travelling
  it, then went silent before reaching the far end, did not leave frame — it left
  the corridor. That is a `corridor_dropout` event.
- **Sector-wide repeat detection.** A global identity is what makes "the same
  thing keeps appearing, in different places, at odd hours" visible at all.

```
CAM-014 ──35s──▶ CAM-022 ──50s──▶ CAM-011
   │                 │                │
   └──── one global entity, handed off twice ────┘

seen at CAM-014 heading north-east → appeared at CAM-022 on time →
never reached CAM-011 → left the corridor → corridor_dropout
```

## Where it runs

On the edge node. A node owns all its cameras, so the coordinator consumes
confirmed tracks from every pipeline directly and needs no sector core. It is the
same principle as everything else here: capability that must survive a dropped
uplink lives on the edge side of the link.

The coordinator is pure logic returning plain findings, which is why it is fully
tested without the pipeline around it (`tests/test_crosscam.py`, 11 tests).
Camera pipelines run in executor threads and all call the one coordinator, so
every mutating entry point is lock-guarded.

## Matching, and why it is conservative

A wrong handoff invents a journey that did not happen, which is worse than two
separate tracks. So an arrival that cannot be matched confidently becomes a new
entity rather than being forced onto a weak candidate.

| Cue | Role |
|---|---|
| **Topology + timing** | Is there an edge into this camera, and is the elapsed time inside its learnt window? |
| **Object class** | A person does not become a car in transit. |
| **Appearance** | An HSV colour signature, split top/bottom, so a dark jacket over light trousers is distinguishable. |

When **both** objects have an appearance signature, the match must actually look
alike (`APPEARANCE_MIN = 0.40`) — good timing alone must never link two visibly
different people. When a signature is **absent** — thermal, night, a crop too
small — the coordinator falls back to timing and class with a *tighter* timing bar
and a discounted score, because it has less to go on and a loose accept there
would invent journeys.

This was found the hard way. An early version weighted timing at 0.55 and
accepted at 0.45, which meant perfect timing alone cleared the bar with no
appearance agreement at all. A test that fed two deliberately different-looking
people through the same corridor caught it.

### Appearance is deliberately coarse, and says so

A proper re-identification model would do this better. That is a download this
build cannot make and a dependency the edge story would rather avoid, so the cue
here is an HSV histogram: cheap, no download, and genuinely discriminative for
the thing that actually separates people across cameras at a distance. It narrows
candidates; it never decides alone. On a thermal camera, where colour is absent,
it degrades to a tone signature and the coordinator leans on timing and class.

**Cross-camera re-ID across heterogeneous optics is genuinely hard** — a subject
on a night-IR camera and on a daylight gate camera will not produce similar
colour histograms. This is a real limitation, not a tuning problem, and a re-ID
model is the honest fix. It is on the roadmap.

## Learned transition times

Every confirmed handoff updates the edge it travelled, so the graph sharpens with
use: a corridor whose walk actually takes 90 seconds converges on 90 seconds
rather than a seeded guess. Edges also track their **arrival rate** — the fraction
of departures that arrived — because a corridor where objects routinely vanish is
itself a signal.

`demo_topology()` seeds a plausible corridor so the reasoning has something to
work with before it has observed a week of traffic. A real deployment learns it.

## Corridor dropout

Raised only for genuine corridor travellers — an entity that has already been
handed off at least once, so it is known to be moving through the sector, and
then goes silent past every plausible arrival window. A single-camera track that
merely ends is **not** a dropout; a test pins that down, because treating every
lost track as a disappearance would bury the real signal.

The event carries the cross-camera trail rather than a clip — the object has
already left — and is sealed into the same hash chain and synchronised like any
other event.

## Verified

- **11 unit tests** covering handoff, the three refusals (too late, wrong class,
  dissimilar appearance), transition-time learning, corridor dropout, and the
  single-camera non-dropout case.
- **Live on the node:** 150 global entities, 2 linked across cameras by confirmed
  handoffs, from ordinary demo traffic with no staging.

## Demonstration

The demo cameras are independent simulated scenes, so no object physically
travels between them and natural handoffs are incidental. The **Demonstration →
corridor journey** action stages one: a subject is injected at each corridor
camera in turn at the topology's transition times, so the coordinator can link
them live.

```bash
curl -X POST localhost:8420/api/demo/action \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"action":"journey"}'
```

Watch **Cross-Camera** in the dashboard.

## API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/crosscam/sector` | Global entities, recent handoffs, topology |
| GET | `/api/crosscam/topology` | The adjacency graph with learned times |
| GET | `/api/crosscam/entities/{id}` | One entity's full cross-camera trail |

## Limitations

- **Appearance matching across very different cameras is weak** (above). A re-ID
  model is the fix.
- **Topology is seeded, not discovered.** A real deployment would learn adjacency
  from observed co-occurrence rather than being told it; here it is configured
  and only the *times* are learned.
- **Entities are held in memory**, not persisted, so the sector view resets on
  node restart. The events it raises are persisted and sealed; the live identity
  graph is not.
- **No appearance-based search.** You cannot yet ask "where else has this person
  been seen" — only follow the links the coordinator already made.
- Group handoffs are treated as individual tracks; a group crossing together is
  not linked as a group.
