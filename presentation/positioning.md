# Prahari — Positioning & Pitch Structure

Fixes two judge critiques: **"novelty is just integration"** (#5) and **"honesty
backfires in a fast round"** (#17). The answer to both is the same: **lead with the
architecture as the contribution, and land the story before the caveats.**

## The one sentence (say this first, memorise it)

> "Detection and tracking are solved — we didn't reinvent them. Prahari's
> contribution is the **deployment architecture** that makes them work on India's
> *open* borders using *existing* CCTV: it **measures what each degraded camera can
> actually do**, applies **pattern-of-life instead of useless tripwires**, and
> produces **tamper-evident evidence that survives an offline uplink**."

That triple — *measured capability + open-border doctrine + offline evidence
integrity* — is the invention. No commercial VMS packages it for the Indian
open-border reality.

## Why "integration" is the wrong dismissal

The novel unit is not a model; it is the **decision architecture** around the
models. Three things nobody ships together:

1. **Measured, not declared, camera capability.** Most systems ask an operator
   what a camera can do. Prahari *measures* effective resolution, delivered fps,
   noise, compression damage, and recovers the ground plane from pedestrians —
   then grants analytics per image region against IEC 62676-4 DORI bands.
   *(Verified: mounting-height recovery within 0.4–3.2% on the live pipeline.)*
2. **Two doctrines, per camera.** Fenced sectors run tripwire rules; open borders
   (Indo-Nepal/Bhutan, lawful daily traffic) run pattern-of-life against a lawful
   route — because a tripwire there fires thousands of times a day and trains the
   operator to ignore it. *(Measured: NormalcyModel drops the lawful-traffic
   false-alert load 34 → 0 while every anomaly still alerts.)*
3. **Evidence that outlives the network.** Append-only SHA-256 hash chain,
   independently verifiable, queued locally through an uplink outage and
   synchronised intact on reconnect. *(Measured: 6/6 evidence-chain PASS, outage
   recovery PASS.)*

Any one of these is a feature. **Together, aimed at open-border reality, they are
the product.**

## Story-led 7-minute structure (do NOT open with caveats)

1. **0:00 — The problem, sharply (45s).** Open border, lawful traffic, alert
   fatigue kills deployments. "Crossing a line is not an intrusion here."
2. **0:45 — The one sentence (15s).** The triple above.
3. **1:00 — Live demo, the arc (3:30).** Normal open-border movement → *suppressed*.
   Patrol deviation → *escalated*. Night movement → *alert*. Open the alert →
   *clip + hash verification*. Cut the network → *local queue*. Restore → *sync,
   integrity intact*. This is the emotional core — let it breathe.
4. **4:30 — Two measured proofs (1:00).** "34 → 0 false alerts with pattern-of-life."
   "Evidence hash chain verified independently." One number each, said with
   confidence.
5. **5:30 — What's real, what's next (1:00).** *Now* the honesty — but framed as a
   **roadmap, not an apology**: "Everything you saw is reproducible. We've been
   deliberate about not overclaiming: this is validated in simulation and on real
   footage; the next step is one real SSB feed and a field pilot." Name the gaps
   as your plan.
6. **6:30 — The ask (30s).** One real border feed / a pilot site. That is the only
   thing standing between this and field validation.

## Turning honesty into an asset (not a liability)

- **Frame caveats as a roadmap slide, near the end** — not scattered through the
  pitch. "Here's exactly what we haven't proven yet, and the shortest path to
  proving it." Judges trust a team that knows its own holes.
- **Lead every metric with the win, then the caveat** — "5× better face detection,
  Apache-licensed and deployable — measured on the validation split." Not
  "val-only, VOC-style, not the official protocol… oh and it's 5× better."
- **Never volunteer a weak number without its context.** Haar's 0.121 only appears
  as the *before* in a 0.121 → 0.626 improvement story, never on its own.
- **The honesty is the differentiator against polished-but-hollow teams.** Say it:
  "You've seen 120 demos today. Ours is the one where every number reproduces from
  a script and every limitation is written down."
