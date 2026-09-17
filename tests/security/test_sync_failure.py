import pytest
import asyncio
from prahari.edge.sync import SyncManager
from prahari.common.db import Database
from prahari.common.models import Event, SyncState, EventType, Priority

@pytest.mark.asyncio
async def test_offline_sync_failure_idempotency(tmp_path):
    """
    Test:
        Offline-sync failure test
    """
    db = Database(str(tmp_path / "test.db"))
    ev = Event(
        event_id="EVT-SYNC",
        node_id="NODE1",
        camera_id="CAM",
        rule_id="R",
        event_type=EventType.ZONE_INTRUSION,
        priority=Priority.HIGH
    )
    db.insert_event(ev)
    
    sync = SyncManager(db, "http://fakecore", "NODE1", enabled=True)
    sync.force_offline = True
    
    await sync.tick()
    assert db.get_event("EVT-SYNC").sync_state == SyncState.PENDING
