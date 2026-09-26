# Prahari — Positioning & Pitch Structure (Blockchain & Cybersecurity)

Theme: **Blockchain & Cybersecurity**. Judges evaluate the *evidence-integrity and
security* contribution first; the AI/CV pipeline is the application that generates
the evidence. So lead with the ledger and the security posture — not with detection.

## The one sentence (say this first, memorise it)

> "Prahari turns existing border CCTV into a **cryptographically verifiable
> chain of custody**: every event is sealed into a **two-tier, countersigned
> hash-ledger** — the edge node writes, the sector core notarizes with a key the
> node never holds — so surveillance evidence is **tamper-resistant and admissible
> under BSA §65B**, even after a three-day uplink outage at a remote outpost."

That is the invention for this theme: **a distributed, tamper-resistant evidence
ledger hardened for a hostile edge**. The computer vision is *how the evidence is
produced*; the blockchain + cybersecurity is *what makes it trustworthy in court*.

## Why this is a Blockchain & Cybersecurity project, not a CV one

The novel unit is the **integrity architecture** around the sensors. What nobody
ships together for border surveillance:

1. **Two-tier permissioned ledger (edge writes → core notarizes).** Each node keeps
   an append-only SHA-256 hash chain; the sector core **countersigns** each node's
   chain head under a secret no node holds and remembers the hash it witnessed.
   Because every entry commits to its predecessor, any rewrite of witnessed history
   changes the head hash and is detected — an insider who re-stamps the whole chain
   is *caught*, not trusted. *(Measured: `tests/security/test_countersign.py` — the
   flagship case is a rewrite the edge chain passes and the notary catches. Edge
   chain 6/6 PASS; notary rewrite-detection PASS.)*
2. **Defence-in-depth for a physically reachable node.** Signed model manifest
   (SHA-256 + HMAC — supply-chain integrity before load); scrypt + HMAC-signed
   expiring tokens + RBAC; **login brute-force lockout**; per-camera sensor-tamper
   detection (spray/blackout/replay). A full **STRIDE threat model**
   ([`../docs/threat-model.md`](../docs/threat-model.md)) marks every control
   Built / Partial / Planned. *(Measured: security suite green — countersign, auth,
   throttle, manifest, evidence-integrity, mTLS-sync, websocket-auth.)*
3. **Integrity that outlives the network.** Idempotent, store-and-forward sync
   keyed on the edge's own event id: a half-delivered batch after a link drop is a
   non-event, not data loss, and the chain synchronises intact. *(Measured:
   offline-sync idempotency PASS; outage recovery PASS.)*

The AI application on top — measured per-camera capability, open-border
pattern-of-life instead of tripwires, cross-camera Re-ID — is real and strong, but
it is the *source* of the evidence, and it belongs after the integrity story.

## The honest line on "is this really blockchain?"

Say it before a judge asks: **"It's a permissioned, two-tier distributed ledger —
hash-linked, append-only, countersigned by an independent core. It is not a
public proof-of-work chain, and it shouldn't be: border evidence must stay inside
government custody, not on a public network. The property that matters — no party,
including a node holding its own key, can rewrite witnessed history undetected — is
achieved and tested."** Then name the next hardening step (asymmetric/HSM core
signing, external anchoring of checkpoint heads) as roadmap, not apology.

## Story-led 7-minute structure (lead with integrity)

1. **0:00 — The problem (45s).** Border evidence is only as good as its custody. A
   painted-over camera reports "all clear"; a tampered clip is worthless in court.
2. **0:45 — The one sentence (15s).** The chain-of-custody line above.
3. **1:00 — Live demo, the arc (3:30).** An event fires → clip sealed into the
   chain. **Verify → CHAIN INTACT.** Core witnesses it → **COUNTERSIGNED (Evidence
   Integrity view).** Now the attack: rewrite a past event so the edge chain *still*
   says intact → the notary says **REWRITE DETECTED.** Then cut the network → local
   queue; restore → sync, integrity intact. Close on the §65B certificate.
4. **4:30 — Two measured proofs (1:00).** "A full-chain rewrite the edge misses, the
   core catches — tested." "Evidence admissible under BSA §65B, certificate
   generated from the ledger." One number each, said with confidence.
5. **5:30 — Security posture (1:00).** The STRIDE slide: what's Built (countersign,
   signed models, auth + lockout, tamper detection), what's Partial/Planned (mTLS,
   HSM, anchoring). Honesty framed as a roadmap.
6. **6:30 — The ask (30s).** One real SSB/CIBMS feed and a pilot site to field-validate.

## Turning honesty into an asset

- **Frame Partial/Planned as a roadmap slide, near the end** — the STRIDE table does
  this for you. Judges trust a team that draws the line exactly where the code does.
- **Lead every claim with the win, then the caveat** — "tamper-resistant via core
  countersigning, tested; the next step is HSM-backed asymmetric signing." Not the
  caveat first.
- **The differentiator against polished-but-hollow teams:** "Every number reproduces
  from a script, every limitation is in the threat model, and the rewrite attack is
  in the test suite — run it."
