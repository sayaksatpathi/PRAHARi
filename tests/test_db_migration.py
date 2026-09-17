import pytest
import sqlite3
from prahari.common.db import Database

def test_db_migration_v2_to_v3(tmp_path):
    db_path = tmp_path / "prahari.sqlite"
    
    # Create a v2 DB schema manually
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 2")
    conn.execute('''
        CREATE TABLE IF NOT EXISTS cameras (
            camera_id TEXT PRIMARY KEY,
            payload TEXT
        )
    ''')
    conn.commit()
    conn.close()
    
    # Initialize Database, which should trigger migrations up to v3
    db = Database(db_path)
    
    # Verify new tables exist
    conn = sqlite3.connect(db_path)
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    tables = [row[0] for row in cursor.fetchall()]
    
    assert "incidents" in tables
    assert "global_entities" in tables
    assert "plates" in tables
    assert "node_state" in tables
    
    # Verify version bumped
    cursor = conn.execute("PRAGMA user_version")
    version = cursor.fetchone()[0]
    assert version >= 3
    
    conn.close()
    db.close()
