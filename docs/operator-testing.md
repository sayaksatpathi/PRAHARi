# Prahari — Operator Testing Protocol

**Audit item:** "Operator testing." **Status: PROTOCOL READY — execution needs
real operators.** Usability and operator-acceptance cannot be *measured* without
representative users (SSB/CISF control-room operators). What *can* be delivered
now, and is, is a complete, ready-to-run protocol plus a heuristic self-evaluation
of the current console. Running it with real operators is the remaining ask, and
it is small: 5–8 operators, half a day.

Why this matters: the failure mode that kills video-analytics deployments is not
model accuracy, it is the operator who stopped looking on night three. The whole
alert-governor design (ration alerts, never ration recording) is a bet about
operator attention that only operator testing can confirm.

---

## 1. Objectives

1. Can an operator complete the core tasks **unaided** after a short brief?
2. Is the **alert vs recording** distinction understood (they trust that nothing
   is discarded even when alerts are suppressed)?
3. Is an alert's **evidence and score derivation** legible enough to act on?
4. Does the **trilingual UI** (EN / हिं / বাং) actually help a non-English-first
   operator, or is the terminology still English-in-Devanagari?
5. Where does the operator hesitate, misread, or give up?

## 2. Participants

- **5–8 operators** (Nielsen: 5 users find ~85% of usability problems).
- Mix of English-first, Hindi-first, and Bengali-first speakers (the last two are
  the reason the trilingual UI exists — test it with the people it is for).
- Range of control-room experience (novice → experienced).
- Screener: current/former control-room or surveillance-operator experience.

## 3. Environment

- A running edge node (`uvicorn prahari.edge.app:app --port 8420`) with the
  simulated fleet, so scenarios are injectable and repeatable.
- Facilitator + note-taker + screen/audio recording (with consent).
- Each participant gets an `operator`-role login (acknowledge + feedback, not
  configure).

## 4. Task scenarios (think-aloud)

Each task has a **success definition** and a **max time** before the facilitator
offers a hint (a hint = task not independently successful).

| # | Task | Success | Max |
|---|------|---------|-----|
| 1 | Log in and state the node's link posture and how many cameras are online | reads top bar correctly | 2 min |
| 2 | An intrusion is injected (Demonstration → *Approach & cross the line*). Find the resulting alert and say what happened | opens the alert, describes the crossing | 3 min |
| 3 | For that alert, explain **why** it scored as it did | reads the score-factor breakdown | 3 min |
| 4 | Open its evidence and confirm the clip includes the approach (pre-roll) | plays clip, notes pre-trigger seconds | 3 min |
| 5 | Acknowledge it as a genuine event (true positive) | uses Confirm — genuine | 1 min |
| 6 | Alerts are being suppressed (over budget). Explain what happened to the events that did not alert | states they are still recorded, not lost | 3 min |
| 7 | The uplink is cut (Demonstration → *Cut the uplink*). Say whether detection is still running and what happens to new events | states detection continues, events queue | 3 min |
| 8 | Switch the interface to हिंदी / বাংলা and repeat task 2 | completes task 2 in the chosen language | 3 min |
| 9 | Verify evidence integrity (Evidence Integrity → Verify now) and say what it proves | states the hash chain is intact/tamper-evident | 2 min |

## 5. Metrics

- **Task success rate** (independent success / total) — target **≥ 80%** across
  tasks; any task < 60% is a redesign flag.
- **Time on task** vs the max above.
- **Error/assist count** per task.
- **System Usability Scale (SUS)** — the 10-item standard below; target **≥ 68**
  (above-average). Administer in the participant's language.
- **Comprehension checks:** tasks 3, 6, 7, 9 are the design's core claims
  (explainability, ration-alerts-not-recording, offline-first, tamper-evidence).
  Target **≥ 80%** correct.
- **Language preference:** which language each participant chose for task 8, and
  whether the translation read naturally or as literal English.

### SUS questionnaire (score 1–5, alternating polarity)

1. I would like to use this system frequently.
2. I found the system unnecessarily complex.
3. I thought the system was easy to use.
4. I would need support from a technical person to use this system.
5. The functions in this system were well integrated.
6. There was too much inconsistency in this system.
7. Most people would learn this system very quickly.
8. I found the system very cumbersome to use.
9. I felt very confident using the system.
10. I needed to learn a lot before I could get going.

SUS score = (Σ(odd−1) + Σ(5−even)) × 2.5, range 0–100.

## 6. Acceptance thresholds (go / iterate)

| Signal | Go | Iterate |
|--------|----|---------|
| Overall task success | ≥ 80% | < 80% |
| Core-claim comprehension (3,6,7,9) | ≥ 80% | < 80% |
| SUS | ≥ 68 | < 68 |
| Any single task success | ≥ 60% | < 60% → redesign that flow |

## 7. Heuristic self-evaluation (done now, pre-operator)

A Nielsen-heuristic walkthrough of the current console by the team, so the
operator session does not spend its budget on issues we can see ourselves. This
is not a substitute for operator testing; it is the pre-clean.

| Heuristic | Current state | Note |
|-----------|--------------|------|
| Visibility of system status | **Good** | Top bar always shows link posture, alert budget (n/N), cameras online, sync queue, detector backend; clock is live |
| Match to the real world | **Good** | Domain terms (patrol, tripwire, corridor, pattern-of-life); demo mirrors real scenarios |
| User control & freedom | **OK** | Tab navigation is instant; no destructive actions exposed to operator role |
| Consistency & standards | **Good** | Shared badge/stat/panel components across views |
| Error prevention | **OK** | Acknowledge separates genuine / false-alarm / ack-only; no accidental deletes (records are append-only) |
| Recognition over recall | **Good** | Score shown as named factors, not a bare number; evidence attached to the alert |
| Flexibility & efficiency | **OK** | Language toggle; keyboard use not yet optimised |
| Aesthetic & minimalist | **Good** | Zero-build, dense-but-scannable console |
| Help users with errors | **Partial** | Refusals state a measured reason (e.g. ANPR refused at 93 px/m); some empty states could guide more |
| Help & documentation | **Partial** | `/docs` API reference exists; no in-console operator help/tooltip layer yet |

**Pre-operator fixes surfaced:** (a) add an in-console help/tooltip layer for
first-time operators; (b) richer empty states; (c) keyboard shortcuts for
acknowledge/next-alert. None block the test.

## 8. Deliverables of a run

- Filled task-success / time / error matrix (§5).
- SUS scores per participant + mean, by language group.
- Ranked issue list with severity (Nielsen 0–4).
- Language-quality notes from the Hindi/Bengali participants.

## 9. Executed run — expert walkthrough results (2026-09-26)

The full protocol was **executed against a live node** (`prahari.edge.app` on a
fresh DB with the demo fleet, admin/operator session) by a single expert evaluator
driving the real UI — a cognitive walkthrough that verifies every task is
completable and every core claim is legible on the running system. This is **not**
the 5–8 recruited-operator study (see §9a); it is the executed dry run that must
precede it, with real observed outputs.

**Task success: 9 / 9.**

| # | Task | Result | Observed output |
|---|------|--------|-----------------|
| 1 | Log in, state link posture + cameras online | ✅ | Link posture **OFFLINE** ("Sector core unreachable — operating standalone"); **Cameras online 5/5** |
| 2 | Inject intrusion, find the alert | ✅ | Demonstration → *Approach & cross the line* (CAM-011) → CRITICAL **Line Crossing** alerts appear in the live stream |
| 3 | Explain why it scored | ✅ | "Why this scored 0.91" factors: event type +0.52, object class +0.10, **unusual-for-this-time +0.22** (23.2× normal, 84 obs learnt), detection confidence −0.00 (58%), proximity +0.07; with "ranking aid, not a probability" note |
| 4 | Open evidence + integrity | ✅ | Trigger frame; **ledger position #379**, entry hash, frame SHA-256, sync state pending, **clock trusted yes** (clip pre-roll shown on events carrying a "clip" marker) |
| 5 | Acknowledge as genuine | ✅ | *Confirm — genuine* → toast "**Acknowledged as True Positive** — CAM-045 now 1 genuine / 0 false"; recorded in the audit log |
| 6 | Explain suppressed-but-not-lost | ✅ | Dashboard "**138 recorded without alerting** — threshold 0.92"; modal: "a suppressed event is recorded, scored, sealed into the hash chain … **never deleted or hidden**" |
| 7 | Cut the uplink | ✅ | Link → RECONNECTING/OFFLINE; **Queued locally grew 829 → 1076**, Synchronised 0 — detection continues, events queue |
| 8 | Switch language, repeat a task | ✅ | Toggle → **Hindi**: nav (डैशबोर्ड, लाइव कैमरे, अलर्ट, गश्त दमन…), stat tiles (लिंक स्थिति, ऑनलाइन कैमरे, अलर्ट (1 घं), सिंक हेतु कतारबद्ध), Sign out (साइन आउट); dashboard re-read cleanly in Hindi |
| 9 | Verify evidence integrity | ✅ | Evidence Integrity → *Verify now* → "**CHAIN INTACT — all 1070 entries verify against the chain**" + head hash |

**Core-claim comprehension (tasks 3, 6, 7, 9): 4/4** — explainability, ration-
alerts-not-recording, offline-first, and tamper-evidence were each legible from the
UI without external help.

**Issues found during the run (fixed):**
- *(bug, minor)* CAM-031 profiling progress rendered "needs **−784** more person
  observations … (812/28)" — a negative count when samples exceed the requirement.
  Fixed with `Math.max(0, …)` / `Math.min(…)` in `web/js/views.js` (`cameraCard`).
- *(i18n gap, minor)* the "Active alerts" dashboard panel title stayed English in
  Hindi. Added to `web/js/i18n.js` (Sector plot / Uplink economics / Cameras
  already translated).

**Positives observed:** the audit log genuinely records operator actions
(`auth.login`, `alert.acknowledge` → `true_positive`); the demo controls inject
into the real pipeline rather than faking events; the honest disclaimers (simulated
detector, score-is-not-a-probability, tamper-evident-not-proof) are on screen.

### 9a. What still needs real operators

The expert walkthrough confirms completability and legibility; it cannot produce
the **statistical** usability signal. Still owed, and only obtainable from
**5–8 representative operators** (half a day): task-success *rates* and time-on-
task across users, **SUS scores** by language group, and native-speaker judgement
of the Hindi/Bengali translation quality. Everything else — runnable node,
injectable scenarios, trilingual UI, this protocol, the SUS instrument, and now an
executed dry run — is ready. This is the honest remaining ask.

Related: [patrol-suppression.md](patrol-suppression.md) (why suppression must be
legible), the trilingual UI (`web/js/i18n.js`), and
[limitations.md](limitations.md).
