"""Core countersigning: the ledger is tamper-RESISTANT, not merely tamper-evident.

The edge hash chain catches a naive edit (a changed field with a stale hash). It
does *not* catch a determined insider who edits an event and then re-stamps every
following ``prev_hash``/``entry_hash`` so the chain is internally consistent again
— ``EvidenceLedger.verify()`` recomputes from content and passes it. These tests
prove that gap exists, and that the core notary closes it: once the core has
countersigned a chain head, that rewrite is detected and refused, because the
attacker can neither reproduce the core's secret nor make the witnessed head hash
match the rewritten one.
"""
from __future__ import annotations

import pytest

from prahari.common.db import Database
from prahari.common.models import Event, EventType, Priority
from prahari.core.notary import LedgerRewriteError, Notary, core_countersign
from prahari.edge.evidence import EvidenceLedger, compute_entry_hash

SECRET = "test-notary-secret-not-held-by-any-node"
NODE = "N1"


@pytest.fixture()
def db(tmp_path) -> Database:
    database = Database(tmp_path / "core.db")
    yield database
    database.close()


def _make_event(i: int) -> Event:
    return Event(event_id=f"EVT-{i:04d}", camera_id="CAM-1", node_id=NODE,
                 event_type=EventType.LINE_CROSSING, priority=Priority.HIGH,
                 priority_score=0.5 + i * 0.01)


def _build_chain(db: Database, n: int) -> EvidenceLedger:
    ledger = EvidenceLedger(db)
    for i in range(1, n + 1):
        db.insert_event(ledger.append(_make_event(i)))
    return ledger


def _restamp_full_rewrite(db: Database, victim_index: int) -> None:
    """Insider attack: edit one event, then re-chain everything from it to the head.

    Produces a chain that is once again internally consistent, so the edge-side
    EvidenceLedger.verify() accepts it. This is the attack the notary must catch.
    """
    entries = db.ledger_entries()  # (index, prev, entry, event_id) ascending
    # prev_hash feeding the victim is the (unchanged) hash of victim_index - 1.
    prev = ""
    for index, _stored_prev, stored_hash, _eid in entries:
        if index == victim_index - 1:
            prev = stored_hash
        if index < victim_index:
            continue
        ev = db.get_event(f"EVT-{index:04d}")
        if index == victim_index:
            ev.priority_score = 0.0001          # the quiet downgrade
        ev.prev_hash = prev
        ev.entry_hash = compute_entry_hash(index, prev, ev)
        db.update_event(ev)
        prev = ev.entry_hash


# --------------------------------------------------------------------------

def test_edge_chain_alone_cannot_stop_a_full_rewrite(db):
    """Honest baseline: the edge chain accepts a fully re-stamped rewrite."""
    ledger = _build_chain(db, 6)
    assert ledger.verify()["valid"]
    _restamp_full_rewrite(db, victim_index=3)
    # The naive tamper test would fail here; the sophisticated rewrite passes.
    assert ledger.verify()["valid"], "re-stamped chain is internally consistent"


def test_notary_witnesses_and_self_verifies(db):
    _build_chain(db, 6)
    notary = Notary(db, SECRET)
    cp = notary.witness(NODE, db.node_ledger_entries(NODE))
    assert cp is not None and cp.ledger_index == 6
    chain = notary.verify_checkpoint_chain(NODE)
    assert chain["valid"] and chain["witnessed_up_to"] == 6
    assert notary.audit_against_events(NODE, db.node_ledger_entries(NODE))["valid"]


def test_notary_catches_the_rewrite_the_edge_chain_misses(db):
    ledger = _build_chain(db, 6)
    notary = Notary(db, SECRET)
    notary.witness(NODE, db.node_ledger_entries(NODE))       # core witnesses head=6

    _restamp_full_rewrite(db, victim_index=3)

    # Edge chain still says "valid" — this is the gap.
    assert ledger.verify()["valid"]
    # Notary says "rewritten": the head hash no longer matches what was witnessed.
    audit = notary.audit_against_events(NODE, db.node_ledger_entries(NODE))
    assert not audit["valid"]
    assert audit["broken_at"] == 6
    # And re-witnessing the rewritten chain is refused outright.
    with pytest.raises(LedgerRewriteError):
        notary.witness(NODE, db.node_ledger_entries(NODE))


def test_witness_is_idempotent_and_advances(db):
    _build_chain(db, 3)
    notary = Notary(db, SECRET)
    first = notary.witness(NODE, db.node_ledger_entries(NODE))
    again = notary.witness(NODE, db.node_ledger_entries(NODE))
    assert first.core_sig == again.core_sig          # head unchanged -> idempotent
    _build_chain_more = EvidenceLedger(db)
    db.insert_event(_build_chain_more.append(_make_event(4)))
    advanced = notary.witness(NODE, db.node_ledger_entries(NODE))
    assert advanced.ledger_index == 4
    assert advanced.core_prev_sig == first.core_sig  # checkpoints are chained


def test_tampering_the_notary_log_is_detected(db):
    _build_chain(db, 4)
    notary = Notary(db, SECRET)
    notary.witness(NODE, db.node_ledger_entries(NODE))
    # Forge the checkpoint content without the secret: recompute is impossible,
    # so the stored core_sig no longer matches.
    db.execute("UPDATE checkpoints SET entry_hash=? WHERE node_id=?",
               ("f" * 64, NODE))
    chain = notary.verify_checkpoint_chain(NODE)
    assert not chain["valid"]


def test_core_secret_is_required_to_forge_a_signature(db):
    _build_chain(db, 2)
    notary = Notary(db, SECRET)
    cp = notary.witness(NODE, db.node_ledger_entries(NODE))
    # A different secret produces a different signature — a node cannot forge it.
    forged = core_countersign("attacker-guess", cp.node_id, cp.ledger_index,
                              cp.entry_hash, cp.witnessed_at, cp.core_prev_sig)
    assert forged != cp.core_sig
