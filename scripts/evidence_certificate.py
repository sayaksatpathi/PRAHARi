"""Generate a BSA §63(4) / IEA §65B(4) electronic-record certificate from the ledger.

Produces the certificate an authorised signatory completes to tender Prahari's
evidence records in an Indian proceeding — populated with the *technical facts* the
node can attest (device identity, the period covered, the verified hash-chain head,
and a per-record SHA-256 table), leaving the human attestation and signature blank.

**This is not legal advice and does not itself make anything admissible.** It fills
the mechanical parts of the certificate from the evidence ledger so counsel and an
authorised signatory can review and sign it. See docs/evidence-admissibility.md.

    python scripts/evidence_certificate.py                       # default edge DB
    python scripts/evidence_certificate.py --db var/prahari-edge.db --out var/evidence_cert.txt
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from prahari.common.config import get_settings          # noqa: E402
from prahari.common.db import Database                   # noqa: E402
from prahari.edge.evidence import EvidenceLedger         # noqa: E402


def build_certificate(db_path: Path) -> dict:
    settings = get_settings()
    db = Database(db_path)
    try:
        ledger = EvidenceLedger(db)
        verify = ledger.verify()
        entries = db.ledger_entries()          # (index, prev_hash, entry_hash, event_id)
        records = []
        first_ts = last_ts = None
        for index, _prev, entry_hash, event_id in entries:
            ev = db.get_event(event_id)
            ts = getattr(ev, "timestamp", None) if ev else None
            if ts is not None:
                first_ts = ts if first_ts is None or ts < first_ts else first_ts
                last_ts = ts if last_ts is None or ts > last_ts else last_ts
            records.append({
                "ledger_index": index,
                "event_id": event_id,
                "entry_sha256": entry_hash,
                "camera_id": getattr(ev, "camera_id", None) if ev else None,
                "event_type": (getattr(ev, "event_type", None).value
                               if ev and getattr(ev, "event_type", None) else None),
                "timestamp": ts.isoformat() if ts else None,
            })
    finally:
        db.close()

    manifest_signed = None
    try:
        from prahari.edge import manifest as _m
        man = _m.load_manifest(ROOT / "models")
        manifest_signed = bool(man.get("signature")) if man else False
    except Exception:                            # noqa: BLE001
        pass

    return {
        "certificate": "Electronic Record Certificate (BSA 2023 §63(4) / IEA §65B(4))",
        "not_legal_advice": ("Auto-populated technical facts only. An authorised "
                             "signatory must review and sign; admissibility is for a "
                             "court, advised by counsel, to determine."),
        "device": {
            "node_id": settings.node_id,
            "software": "Prahari edge node",
            "detector": str(getattr(settings, "detector", "auto")),
            "produced_in_regular_use": True,
        },
        "period_covered": {
            "from": first_ts.isoformat() if first_ts else None,
            "to": last_ts.isoformat() if last_ts else None,
        },
        "integrity": {
            "method": "append-only SHA-256 hash chain (each entry commits to the prior)",
            "chain_valid": bool(verify.get("valid")),
            "entries": verify.get("entries", 0),
            "chain_head_sha256": verify.get("head"),
            "verify_message": verify.get("message"),
            "model_manifest_signed": manifest_signed,
        },
        "records": records,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "attestation": {
            "signatory_name": "____________________",
            "designation": "____________________",
            "responsible_for_device": "____________________",
            "statement": ("I certify that the above electronic records were produced "
                          "by the Prahari computer system in its regular course of "
                          "operation, that the system was operating properly (or any "
                          "malfunction did not affect the records' accuracy), and that "
                          "the SHA-256 hash-chain head above corresponds to the records "
                          "as produced."),
            "signature": "____________________",
            "date": "____________________",
        },
    }


def render_text(cert: dict) -> str:
    L = []
    L.append(cert["certificate"])
    L.append("=" * len(cert["certificate"]))
    L.append("")
    L.append("NOTE: " + cert["not_legal_advice"])
    L.append("")
    d = cert["device"]
    L.append(f"Device / system : {d['software']} (node {d['node_id']}, detector {d['detector']})")
    p = cert["period_covered"]
    L.append(f"Period covered  : {p['from']}  to  {p['to']}")
    i = cert["integrity"]
    L.append(f"Records         : {i['entries']}")
    L.append(f"Integrity       : {i['method']}")
    L.append(f"Hash-chain valid: {i['chain_valid']}  ({i['verify_message']})")
    L.append(f"Chain head      : {i['chain_head_sha256']}")
    L.append(f"Model manifest signed: {i['model_manifest_signed']}")
    L.append("")
    L.append("Per-record SHA-256 (ledger order):")
    for r in cert["records"][:200]:
        L.append(f"  #{r['ledger_index']:<5} {r['event_id']:<18} {r['entry_sha256']}")
    if len(cert["records"]) > 200:
        L.append(f"  … and {len(cert['records']) - 200} more (see the JSON)")
    L.append("")
    a = cert["attestation"]
    L.append("ATTESTATION (to be completed and signed by the authorised person):")
    L.append(f"  Name            : {a['signatory_name']}")
    L.append(f"  Designation     : {a['designation']}")
    L.append(f"  Responsible for : {a['responsible_for_device']}")
    L.append("")
    L.append("  " + a["statement"])
    L.append("")
    L.append(f"  Signature: {a['signature']}    Date: {a['date']}")
    return "\n".join(L)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=None, help="edge DB (default: settings.db_path)")
    ap.add_argument("--out", type=Path, default=ROOT / "var/evidence_certificate.txt")
    args = ap.parse_args()

    db_path = args.db or Path(get_settings().db_path)
    if not Path(db_path).exists():
        print(f"FAIL: edge DB not found at {db_path}. Run a node first, or pass --db.")
        return 1

    cert = build_certificate(Path(db_path))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render_text(cert), encoding="utf-8")
    args.out.with_suffix(".json").write_text(json.dumps(cert, indent=2), encoding="utf-8")
    ok = cert["integrity"]["chain_valid"]
    print(f"records={cert['integrity']['entries']} chain_valid={ok} "
          f"head={str(cert['integrity']['chain_head_sha256'])[:16]}…")
    print(f"wrote {args.out} and {args.out.with_suffix('.json')}")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
