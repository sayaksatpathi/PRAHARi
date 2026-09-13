# Security and threat model

A border surveillance node is a target. The threat model here assumes an
adversary who can reach the camera physically and may be able to reach the
network, and it is explicit about which defences are built and which are designed
for but not implemented.

## The sensor is the soft target

An adversary at a border attacks the camera, not the algorithm. Spray paint on a
dome, a lens turned skyward, an IR illuminator defeated with a torch, a cut PoE
run — or the quiet one, a looped recording fed back into the recorder so the
screen shows a peaceful stretch of fence that is no longer there.

None of that is exotic, and **an object detector will not notice any of it**. It
will report "no objects" on a camera that has been painted over, with complete
confidence. So integrity is monitored separately, on its own signals
(`edge/tamper.py`), and raises its own high-priority event class that is never
rate-limited.

| Attack | Signal used | Status |
|---|---|---|
| Lens covered / sprayed | Brightness collapse **relative to the camera's own learnt baseline**, plus loss of structure and loss of temporal variation | Built |
| Glare / IR blinding | Saturation relative to baseline with variance collapse | Built |
| Defocus, fogging | Edge-energy collapse while the scene still varies | Built |
| Camera repointed or rotated | Sustained wholesale divergence from an adaptive scene fingerprint | Built |
| Crudely frozen or looped feed | Byte-identical consecutive frames — impossible on a live sensor, because real sensor noise guarantees otherwise | Built |
| Competent replay of a long genuine recording | Needs a challenge the camera cannot precompute | **Not built** |
| Cut cable / power loss | Read failure and health transition | Built |

**A note on the baseline.** The covered-lens check was originally an absolute
brightness threshold, and it reported the thermal and night-IR cameras as tampered
within a minute of startup — because a mean luminance of 17 *is* normal for a
thermal unit. Tampering is a departure from a camera's own normal, not from a
global idea of a correct image. The detector now learns each camera's baseline
before making any judgement, which is the same principle the rest of the system
runs on.

## Evidence integrity

Every event is appended to a hash chain: each entry commits to the hash of the one
before it, so altering or removing any event breaks every link that follows. This
exists specifically for the offline case — a node disconnected for three days is
asking the sector core to accept a backlog on trust, and the chain makes that
checkable.

Committed to: event id, node, camera, type, timestamps (wall and monotonic),
object, track, zone, score, and the SHA-256 of both the trigger frame and the
clip. Deliberately **not** committed to: acknowledgement, sync state and operator
feedback, so that working the alert queue does not invalidate the evidence. A
test asserts exactly that.

**This is tamper-evident, not tamper-proof.** Anyone holding the node's key
material could recompute a consistent chain. Making it tamper-resistant needs:

- hardware-backed keys (TPM or a secure element) so the signing key cannot be
  extracted from a node an adversary has physical access to;
- countersigning at the sector core, so the core's record of a chain head cannot
  be rewritten by whoever holds the edge key;
- periodic publication of chain heads, so a wholesale replacement of a node's
  history is detectable even with the key.

None of those are implemented. The limitation is stated in the module, in the
dashboard's Evidence Integrity view, and in the README.

## Authentication and authorisation

- **Passwords**: scrypt with a per-user random salt (n=2^14, r=8, p=1). Constant-
  time comparison — a timing oracle on the administrator password of a border
  surveillance node is not a theoretical concern. An unknown username burns
  comparable time so the response does not reveal which accounts exist.
- **Tokens**: HMAC-SHA256 signed, expiring bearer tokens. Signature and expiry are
  both verified; tests cover a wrong key, a tampered body and an expired token.
- **Bootstrap**: the first administrator password is generated randomly, printed
  once to the console, and never defaulted to anything guessable.
- **Roles**: `viewer` < `operator` < `admin`, enforced as a FastAPI dependency at
  each endpoint rather than checked in handler bodies.

## Credential handling

Camera passwords never leave the process. `Camera.public_dict()` strips username
and password and redacts credentials embedded in the stream URL; it is applied at
the model rather than at each call site so it cannot be forgotten. Stream URLs are
redacted in log output too (`sources/stream.py:redact`). A test asserts a known
password never appears in serialised output.

`PRAHARI_SECRET_KEY` must be set to a long random value in any real deployment.
The default is a clearly-labelled development value.

## Known weaknesses

Stated rather than buried.

**The MJPEG preview endpoint is unauthenticated.** Browsers cannot attach an
Authorization header to an `<img>` tag, and the usual workaround — a token in the
query string — writes credentials into every proxy and access log between the
node and the operator. The endpoint streams live video and no stored data, and
the node is expected to sit on an isolated operations network. The proper fix is
short-lived signed stream URLs, and it is not built.

**No transport security by default.** The node serves plain HTTP on localhost. A
real deployment needs TLS, and edge-to-core needs mTLS with per-node certificates.

**No rate limiting on authentication.** Brute-force protection is absent.

**Model artifacts are not signed.** A swapped detection model is a supply-chain
attack with no defence here. Signed model artifacts with verification at load are
designed for and not implemented.

## What a real deployment needs

- **Edge dials out only.** A border outpost sits behind NAT on a VSAT or cellular
  link and should expose no inbound port. The sync design already has the edge
  initiate every connection; it needs mTLS and per-node identity to be meaningful.
- **Signed model and configuration artifacts**, with an air-gapped update path
  over USB for sites with no usable link.
- **Secrets from a managed store**, not from a `.env` file on disk.
- **Full-disk encryption** on the edge node. Everything above assumes an
  adversary who can reach the camera; a node in a hut at a remote outpost should
  assume one who can reach the box.
- **A defined incident process** for a camera that reports tampering, because the
  detection is only as useful as the response to it.

## Reporting

This is a prototype built for SIH26187 and is not deployed anywhere. If it becomes
operational, security issues should go to the deploying organisation through its
own disclosure process rather than to a public tracker.
