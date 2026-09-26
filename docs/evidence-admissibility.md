# Prahari — Evidence Admissibility Self-Assessment

**Audit item:** "Evidence not legally/forensically reviewed." **Status: structured
self-assessment done; formal legal opinion still required.**

> **This is not legal advice.** It is an engineering self-assessment that maps
> Prahari's evidence design to the statutory requirements for electronic records in
> India, so qualified legal/forensic counsel can review it efficiently. Whether any
> Prahari record is *admissible* in a given proceeding is a determination only a
> court, advised by counsel, can make.

## 1. The governing law (India)

Electronic-record admissibility in India is governed by:

- **Bharatiya Sakshya Adhiniyam, 2023 (BSA) — §63** (in force from 1 July 2024;
  replaced the Indian Evidence Act, 1872). Electronic records are admissible as
  documents subject to conditions, with a **certificate** (BSA §63(4), Schedule)
  analogous to the former **IEA §65B(4)**.
- **IEA §65B** (legacy) — the predecessor certificate regime; cited because most
  case law (e.g. *Anvar P.V. v. P.K. Basheer*; *Arjun Panditrao Khotkar v. Kailash
  Kushanrao Gorantyal*) is framed under it and carries over.
- **Information Technology Act, 2000** — §§3/3A (digital/electronic signatures),
  §79A (Examiner of Electronic Evidence), which bear on integrity and expert proof.

## 2. What each requirement needs, and where Prahari stands

| Requirement (BSA §63 / IEA §65B lineage) | What it needs | Prahari status |
|------------------------------------------|---------------|----------------|
| Record produced by a computer in regular use | provenance of the producing system | **Design supports** — every event carries `camera_id`, node id, model/detector identity, and timestamps; audit log records issuance and actions |
| **Integrity** of the electronic record | tamper detection | **Strong** — append-only SHA-256 hash chain; each entry commits to the prior hash, so any alteration/removal breaks every later link; independently re-verifiable (`scripts/verify_evidence.py`, dashboard "Verify now" → CHAIN INTACT) |
| Contents unaltered / authentic | hashes over the actual media | **Design supports** — per-event frame SHA-256 stored in the ledger alongside metadata |
| **Reliable timestamps** | trustworthy time, drift handling | **Partial** — clock-trusted flag per event; offline drift corrected at the core without overwriting node observations. A trusted/synced time source (NTP/GNSS) at deployment strengthens this |
| **§63(4) / §65B(4) certificate** | a signed human certificate identifying the device and process | **NOT PROVIDED** — this is a human/legal artifact, not a code output. Prahari must generate a certificate template populated from the ledger; issuing/signing it is an operator/authority act |
| Chain of custody | who handled the record, when | **Partial** — the audit log + hash chain evidence *system-side* custody; physical/organisational custody (export, transfer, storage) is a procedure the deploying force must define |
| Tamper-**proof** (vs evident) | keys an insider cannot forge | **NOT MET (documented)** — "tamper-evident, not tamper-proof": anyone holding the node's key material could forge a consistent chain. Hardware-backed keys (TPM/HSM) + core countersigning are designed-for, not built |

## 3. Honest limitations (already documented, restated for counsel)

- **Tamper-evident, not tamper-proof** ([limitations.md](limitations.md)). Integrity
  detects outside alteration and accidental corruption; it does not stop a holder of
  the node key from forging a self-consistent chain. Hardware-backed keys and
  countersignature at the core close this and are specified, not implemented.
- **No §63(4)/§65B(4) certificate generator** yet — see §4.
- **Replay detection is crude** (byte-identical frames only).
- **No court has ruled** on a Prahari record; no forensic examiner (IT Act §79A) has
  certified the toolchain.

## 4. Concrete next steps (what would make this court-ready)

1. **Certificate generator** — **BUILT.** `scripts/evidence_certificate.py` emits a
   BSA §63(4)/§65B(4)-shaped certificate populated from the ledger — device identity,
   period covered, verified hash-chain head, and a per-record SHA-256 table — with a
   blank attestation block for an authorised signatory to complete and sign. Verified
   on a live ledger (796 records, chain valid). It fills the mechanical parts only;
   the human attestation and the admissibility determination remain with the
   signatory and the court. Run: `python scripts/evidence_certificate.py`.
2. **Hardware-backed keys + core countersignature** — moves tamper-evident →
   tamper-resistant (roadmap; [security-validation.md](security-validation.md)).
3. **Trusted time source** at deployment (NTP/GNSS) and record it per event.
4. **Legal opinion** from counsel on admissibility under BSA §63 for the intended
   proceedings, and **forensic review** by an IT Act §79A Examiner of Electronic
   Evidence. *This is the external ask that only qualified professionals can fulfil.*

## 5. Verify the technical claims

```bash
python scripts/verify_evidence.py           # walk & verify the hash chain
# or in the console: Evidence Integrity -> Verify now  ->  "CHAIN INTACT"
```

Related: [security-validation.md](security-validation.md),
[limitations.md](limitations.md), [recovery-validation.md](recovery-validation.md).
