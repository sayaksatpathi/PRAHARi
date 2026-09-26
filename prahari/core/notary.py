"""Core-side evidence notary — countersigning that makes the ledger tamper-RESISTANT.

Each edge node keeps its own SHA-256 hash chain (``prev_hash`` -> ``entry_hash``).
That chain is tamper-*evident*: any single mutation breaks the link. But it is not
tamper-*proof* on its own — a party holding the node's key can rewrite the whole
chain, recomputing every ``entry_hash`` so the rewritten history is once again
internally consistent, and the node's own ``/api/ledger/verify`` would pass it.

The notary closes exactly that gap. When the sector core ingests a node's events
it **countersigns the node's chain head** with a secret held only by the core
(``core_notary_secret``), and it **remembers the entry_hash it witnessed** at that
index. Because ``entry_hash`` at index *i* commits to ``prev_hash`` (the previous
entry's hash), any change to any entry <= N necessarily changes the head hash at
index N. So once the core has witnessed (N, H):

* a node that later re-presents index N with a different hash is caught
  (``LedgerRewriteError``) — the presented hash no longer matches the witnessed one;
* the node cannot hide the rewrite by forging the witness, because it does not
  hold ``core_notary_secret``.

The checkpoints are themselves hash-chained (``core_prev_sig`` -> ``core_sig``),
so the notary log is an append-only ledger of its own — a two-tier, permissioned
distributed ledger: edge nodes write, the core notarizes. This is the honest
answer to "where is the consensus / immutability?" — the core is the witness, and
immutability is anchored the moment it countersigns.

Full cryptographic hardening (hardware-backed core keys, asymmetric signatures so
a compromised core cannot forge, external anchoring of the checkpoint head) is
laid out in ``docs/security.md``; this module implements the symmetric-HMAC tier,
which is the part that runs without an HSM.
"""
from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def core_countersign(secret: str, node_id: str, ledger_index: int, entry_hash: str,
                     witnessed_at: str, core_prev_sig: str) -> str:
    """Deterministic HMAC-SHA256 countersignature over a witnessed chain head.

    The signature binds the node, the index, the exact content hash, the moment
    of witnessing, and the previous checkpoint signature (so checkpoints form
    their own append-only chain). ``|`` cannot appear in any field (ids are
    slugs, hashes are hex, timestamps are ISO), so the join is unambiguous.
    """
    payload = f"{node_id}|{ledger_index}|{entry_hash}|{witnessed_at}|{core_prev_sig}"
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"),
                    hashlib.sha256).hexdigest()


class LedgerRewriteError(Exception):
    """A node presented a different entry_hash at an index the core already witnessed."""

    def __init__(self, node_id: str, ledger_index: int, witnessed: str, presented: str):
        self.node_id = node_id
        self.ledger_index = ledger_index
        self.witnessed = witnessed
        self.presented = presented
        super().__init__(
            f"ledger rewrite detected for node {node_id} at index {ledger_index}: "
            f"core witnessed {witnessed[:12]}… but node now presents {presented[:12]}…"
        )


@dataclass
class Checkpoint:
    node_id: str
    ledger_index: int
    entry_hash: str
    witnessed_at: str
    core_prev_sig: str
    core_sig: str


class Notary:
    """Countersigns node chain heads and detects history rewrites.

    ``db`` is the core Database; ``secret`` is ``SETTINGS.core_notary_secret``.
    """

    def __init__(self, db: Any, secret: str) -> None:
        self.db = db
        self.secret = secret

    # -- witnessing ------------------------------------------------------
    def witness(self, node_id: str, entries: list[tuple[int, str]],
                now: datetime | None = None) -> Checkpoint | None:
        """Witness a node's chain head, refusing any rewrite of witnessed history.

        ``entries`` is the node's integrity-verified chain as ``(ledger_index,
        entry_hash)`` pairs, ascending. Raises ``LedgerRewriteError`` if any
        presented index that the core has already checkpointed now carries a
        different hash. Otherwise advances the head checkpoint (idempotent if the
        head has not moved) and returns it.
        """
        if not entries:
            return None
        entries = sorted(entries)

        # 1. Rewrite detection: every index the core already witnessed must still
        #    present the identical content hash.
        for index, entry_hash in entries:
            cp = self.db.checkpoint_at(node_id, index)
            if cp is not None and not hmac.compare_digest(cp["entry_hash"], entry_hash):
                raise LedgerRewriteError(node_id, index, cp["entry_hash"], entry_hash)

        # 2. Advance the head checkpoint.
        head_index, head_hash = entries[-1]
        latest = self.db.latest_checkpoint(node_id)
        if latest is not None and latest["ledger_index"] >= head_index:
            return Checkpoint(**latest)  # nothing new to witness

        witnessed_at = _iso(now or datetime.now(timezone.utc))
        prev_sig = latest["core_sig"] if latest else ""
        sig = core_countersign(self.secret, node_id, head_index, head_hash,
                               witnessed_at, prev_sig)
        self.db.append_checkpoint(node_id, head_index, head_hash, witnessed_at,
                                  prev_sig, sig)
        return Checkpoint(node_id, head_index, head_hash, witnessed_at, prev_sig, sig)

    # -- verification ----------------------------------------------------
    def verify_checkpoint_chain(self, node_id: str) -> dict[str, Any]:
        """Recompute every countersignature and confirm the notary log is intact.

        Detects tampering with the checkpoint table itself: a forged or edited
        ``core_sig`` fails the HMAC recomputation, and a broken ``core_prev_sig``
        link fails the chain check. Returns witnessed head and validity.
        """
        cps = self.db.list_checkpoints(node_id)
        prev_sig = ""
        for cp in cps:
            expected = core_countersign(self.secret, cp["node_id"], cp["ledger_index"],
                                        cp["entry_hash"], cp["witnessed_at"],
                                        cp["core_prev_sig"])
            if not hmac.compare_digest(expected, cp["core_sig"]):
                return {"valid": False, "checkpoints": len(cps),
                        "broken_at": cp["ledger_index"],
                        "message": "checkpoint signature does not verify — the "
                                   "notary log was altered"}
            if not hmac.compare_digest(cp["core_prev_sig"], prev_sig):
                return {"valid": False, "checkpoints": len(cps),
                        "broken_at": cp["ledger_index"],
                        "message": "checkpoint predecessor link broken — a "
                                   "checkpoint was removed or reordered"}
            prev_sig = cp["core_sig"]
        return {"valid": True, "checkpoints": len(cps),
                "witnessed_up_to": cps[-1]["ledger_index"] if cps else 0}

    def audit_against_events(self, node_id: str, entries: list[tuple[int, str]]) -> dict[str, Any]:
        """Cross-check current node events against what the core witnessed.

        Read-only counterpart to :meth:`witness`: reports (does not raise) whether
        the events now on record still match every countersigned checkpoint. Used
        by the ledger-verify endpoint so a rewrite shows up as a hard failure even
        when no new batch is being ingested.
        """
        by_index = dict(entries)
        for cp in self.db.list_checkpoints(node_id):
            presented = by_index.get(cp["ledger_index"])
            if presented is not None and not hmac.compare_digest(cp["entry_hash"], presented):
                return {"valid": False, "broken_at": cp["ledger_index"],
                        "witnessed": cp["entry_hash"], "presented": presented,
                        "message": "an event on record no longer matches the hash "
                                   "the core countersigned — history was rewritten"}
        return {"valid": True}
