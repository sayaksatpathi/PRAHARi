# Prahari — Threat Model (STRIDE)

Scope: the Prahari edge node, the sector core, and the edge→core link, deployed on
an open border (India–Nepal / India–Bhutan) reusing existing CCTV. This is the
formal companion to [security.md](security.md): security.md is the prose and the
built/not-built ledger; this file is the structured STRIDE decomposition a
reviewer can check component-by-component. Every control is marked **Built**,
**Partial**, or **Planned**, and Planned is never dressed up as Built.

## 1. Assets worth protecting

| Asset | Why it matters | Primary threat |
|---|---|---|
| Evidence records (events + hashes) | May be put before a court under BSA §63/§65B | Tampering, repudiation |
| Evidence clips/frames | The visual proof behind an alert | Tampering, disclosure |
| The hash chain + notary checkpoints | The thing that makes evidence *trustworthy* | Tampering, forgery |
| Detection model weights | A swapped model blinds the node silently | Supply-chain tampering |
| Camera credentials | Reused across a site; leak = network foothold | Information disclosure |
| Operator/admin accounts | Control of the surveillance picture | Spoofing, elevation |
| Live video + the sensor itself | The ground truth | Tampering (physical), DoS |

## 2. Trust boundaries

1. **Camera ↔ edge node** — the sensor is physically reachable by an adversary at
   the border; its feed is not trusted to be genuine (replay, tamper).
2. **Edge node ↔ sector core** — a metered, intermittent, hostile-transit link
   (VSAT / cellular). The edge dials out; the core exposes the ingest surface.
3. **Operator ↔ node/core API** — authenticated, role-gated.
4. **Edge node internal** — an adversary with physical access may reach the box,
   not just the camera (full-disk encryption is a Planned control).

## 3. STRIDE by component

### 3.1 Evidence ledger (edge `evidence.py` + core `notary.py`)

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **T**ampering | Edit/delete an event | SHA-256 hash chain; any break is detected by `verify()` | **Built** |
| **T**ampering | Insider re-stamps the whole chain into a consistent rewrite | Core countersigns each node's chain head under a key no node holds and remembers the witnessed hash; rewrite of witnessed history is detected and refused | **Built** |
| **T**ampering | Forge/edit the notary checkpoint log | Checkpoints are hash-chained and HMAC-verified (`verify_checkpoint_chain`) | **Built** |
| **T**ampering | Compromised **core** forges witnesses | Symmetric HMAC — a fully compromised core could forge; asymmetric/HSM signing removes this | **Planned** |
| **R**epudiation | "That event was never recorded / was altered" | Chain + countersign + `/api/ledger/verify`; §65B certificate generated from the ledger | **Built** |
| **R**epudiation | Wholesale replacement of a node's history after core compromise | External anchoring of the checkpoint head (append-only publish) | **Planned** |
| **I**nfo disclosure | Evidence at rest readable on a stolen box | DB field encryption (Fernet) for sensitive payloads; full-disk encryption | **Partial** |

### 3.2 Sensor / camera feed (`edge/tamper.py`)

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **T**ampering | Lens sprayed/covered, repointed, defocused, IR-blinded | Per-camera learnt-baseline tamper detection; own high-priority, never-rate-limited event class | **Built** |
| **T**ampering | Crude frozen/looped feed | Byte-identical consecutive-frame detection (live sensor noise makes this impossible) | **Built** |
| **T**ampering | Competent replay of a long genuine recording | Needs a challenge the camera cannot precompute | **Planned** |
| **D**oS | Cut cable / power | Read-failure health transition → OFFLINE + event | **Built** |

### 3.3 Edge → core link (`edge/sync.py`, core `ingest`)

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **T**ampering | Alter events in transit | Chain verified on ingest; countersign refuses altered re-presentation | **Built** |
| **S**poofing | Impersonate a node / the core | Bearer token on sync (`core_token`); **mTLS with per-node certs** is the real control | **Partial** |
| **R**epudiation | Duplicate/replayed batches after a link drop | Idempotent ingest keyed on the edge's own `event_id` (test-covered) | **Built** |
| **I**nfo disclosure | Sniff the link | TLS/mTLS | **Planned** |
| **D**oS | Flood the ingest endpoint | Batch size caps + backpressure; auth; rate limiting | **Partial** |
| **T**ampering | Clock forgery to reorder the timeline | Node clock + monotonic reading carried per batch; core records a correction *alongside* the original, never overwriting | **Built** |

### 3.4 Model supply chain (`edge/manifest.py`)

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **T**ampering | Swap a weights file to blind a class | SHA-256 verify against manifest before load | **Built** |
| **T**ampering | Swap the manifest too | HMAC-SHA256 signed manifest (`verify_signature`) | **Built** |
| **T**ampering | Compromised signer | Asymmetric supply-chain signature (Sigstore-style) | **Planned** |

### 3.5 AuthN / AuthZ (`edge/auth.py`)

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **S**poofing | Guess/steal a password | scrypt (n=2¹⁴,r=8,p=1) + per-user salt; constant-time compare; unknown-user burns comparable time | **Built** |
| **S**poofing | Forge/replay a token | HMAC-SHA256 signed bearer token; signature + expiry verified; `jti` nonce | **Built** |
| **E**levation | Act above one's role | `viewer < operator < admin` enforced as a FastAPI dependency per endpoint | **Built** |
| **S**poofing | Brute-force login | Rate limiting on auth | **Planned** |
| **I**nfo disclosure | Default/guessable admin | Bootstrap admin password generated randomly, printed once | **Built** |

### 3.6 Credentials & secrets

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **I**nfo disclosure | Camera password leaks via API/logs | `Camera.public_dict()` strips + redacts at the model; stream-URL redaction in logs; test asserts a known password never serialises | **Built** |
| **I**nfo disclosure | Weak app secret in prod | `PRAHARI_SECRET_KEY` / `PRAHARI_CORE_NOTARY_SECRET` required; defaults clearly labelled dev-only | **Partial** (managed secret store is Planned) |

### 3.7 Live preview surface

| STRIDE | Threat | Control | Status |
|---|---|---|---|
| **I**nfo disclosure | Unauthenticated MJPEG preview | Streams live video only, no stored data; expected on an isolated ops network; short-lived signed stream URLs are the fix | **Planned** |

## 4. Residual risk (stated, not buried)

The honest headline for a **Blockchain & Cybersecurity** review: the ledger is
tamper-**resistant** via core countersigning today, on a **symmetric** trust root.
The remaining hardening — asymmetric/HSM-backed core signing, external anchoring of
checkpoint heads, mTLS with per-node identity, full-disk encryption, auth rate
limiting, and a managed secret store — is designed for and scoped in
[security.md](security.md) §"What a real deployment needs". None of it is claimed
as built. That distinction is the point: a jury trusts a team that draws the line
exactly where the code does.
