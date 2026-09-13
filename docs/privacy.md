# Privacy and responsible use

Border surveillance is exactly the setting where "we built it because we could"
is the wrong answer. This document records the decisions taken and, more
usefully, the ones deliberately not taken.

## Face recognition is not implemented, on purpose

Never granted automatically, on any camera, at any measured quality. The refusal
reason surfaces in the API and the dashboard:

> Face recognition is never granted automatically by Prahari. It is withheld as a
> matter of product policy, not capability: identity recognition carries legal,
> privacy and authorisation requirements that a video analytics layer cannot
> satisfy on its own.

Two reasons, both worth stating plainly.

**It would not work.** Prahari's own profiling shows that almost no perimeter
camera reaches the IEC 62676-4 *identify* band (250 px/m) anywhere in frame.
Shipping face recognition on cameras that cannot support it produces
identifications that look confident and are wrong, which in this domain is worse
than no identification at all.

**It is not ours to authorise.** Identity recognition against a watchlist engages
legal authority, data protection obligations under the DPDP Act 2023, retention
rules, and accountability for a match being acted upon. Those are decisions for
the deploying organisation under its own legal framework, not defaults baked into
an analytics layer by its authors.

Face *detection* — noticing a face is present, without identifying whose — is a
separate capability, gated at the *recognise* band, and granted only where the
imagery supports it and the role justifies it.

## The distinction that matters

Kept rigorously separate throughout the code and the interface:

| | What it means | What it does **not** mean |
|---|---|---|
| **Detection** | An object of some class is present | Who or what specifically |
| **Tracking** | The same object across frames, with a temporary id | A persistent identity; ids are per camera and discarded |
| **Recognition** | Matching against a known identity | Not implemented |

Track ids are local to one camera's tracker, reused after the track retires, and
carry no meaning beyond that session. They are not identifiers of people.

## Confidence is not certainty

A detector confidence of 0.95 means the model is confident about a *class*. It
says nothing about identity and nothing about intent. The interface labels it
"detector confidence" and never as a probability that something is an intrusion.

Likewise the **Event Priority Score** is a ranking aid with a visible derivation,
not a calibrated probability. It has not been validated against border ground
truth, and the event detail panel says so on screen rather than only in the
documentation.

## Data minimisation

- **No raw video is retained by default.** Continuous recording is not
  implemented. What persists is: an event record, one trigger frame, one
  thumbnail, and a short clip around the trigger.
- **Retention is configurable** (`PRAHARI_EVIDENCE_RETENTION_DAYS`, default 30)
  and enforced by `EvidenceStore.purge_older_than`.
- **The pattern-of-life model stores counts, not people.** A bucket is
  (camera, zone, object class, hour-of-week) → a number. It cannot be queried for
  who was where; it does not retain trajectories, appearance or identity.
- **No cross-camera re-identification** is implemented. Cross-camera handoff is on
  the roadmap and would need its own privacy assessment before being built, since
  it is precisely the capability that turns anonymous detections into a movement
  profile.

## Credentials

Camera passwords are stored in the node database and **never** leave the process.
`Camera.public_dict()` strips the password and username and redacts credentials
embedded in the stream URL, and it is applied at the model rather than at each
call site so it cannot be forgotten. A test asserts a known password never
appears in the serialised output.

The initial administrator password is generated randomly, printed once to the
console, and stored only as an scrypt hash.

## Audit

Every configuration change, camera test, re-profile, login attempt and alert
acknowledgement is written to an append-only audit log with actor, action, target
and timestamp. Available at `/api/system/audit` to administrators and shown in
the Evidence Integrity view.

The evidence hash chain provides the complementary property for events
themselves: the record cannot be quietly edited after the fact without the chain
reporting it.

## Access control

Three roles. `viewer` reads. `operator` additionally acknowledges alerts and
submits feedback. `admin` additionally configures cameras, zones and retention.
Acknowledgement records who acknowledged and when.

## What a real deployment still has to do

This is a prototype, and the following are properly the deploying
organisation's decisions, not defaults an analytics layer should assume:

- A lawful basis and internal authorisation for the surveillance itself.
- A privacy impact assessment covering the specific cameras and their fields of
  view — particularly any that overlook private property, dwellings or civilian
  routes rather than the border itself.
- Retention periods set against actual legal requirements rather than the
  30-day default.
- A decision on whether face detection is enabled at all, and where.
- Signage and notification obligations where they apply.
- Rules on who may export evidence and under what process.

## Evidence and legal admissibility

The hash chain and the per-file SHA-256 digests are designed so an evidence
package carries an integrity statement. Whether that satisfies the requirements
for electronic records under the **Bharatiya Sakshya Adhiniyam, 2023** is a legal
question this project does not attempt to answer, and anyone intending to rely on
Prahari evidence in proceedings should verify the current requirements and
certification process with counsel rather than taking this paragraph as guidance.

What is implemented is tamper-*evidence*: alteration is detectable. It is not
tamper-*proof*, and the limitation is stated wherever the feature is described.
