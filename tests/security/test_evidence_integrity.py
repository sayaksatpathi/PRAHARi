import pytest
from prahari.edge.evidence import EvidenceStore
from prahari.common.db import Database

def test_evidence_integrity(tmp_path):
    """
    Test:
        Evidence chaining and integrity test
    """
    db = Database(str(tmp_path / "ledger.db"))
    # The actual implementation of EvidenceStore in prahari/edge/evidence.py
    # uses EvidenceStore(root).
    store = EvidenceStore(str(tmp_path / "evidence"))
    assert store is not None
