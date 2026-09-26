import pytest
import sqlite3
import subprocess
import sys
from pathlib import Path

def test_backup_and_restore_e2e(tmp_path):
    # Setup live DB
    live_db_path = tmp_path / "live.sqlite"
    conn = sqlite3.connect(live_db_path)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, val TEXT)")
    conn.execute("INSERT INTO test (val) VALUES ('hello')")
    conn.commit()
    conn.close()
    
    # Run backup_db.py (repo root is tests/'s parent, not its grandparent)
    backup_path = tmp_path / "backup.sqlite"
    script_path = Path(__file__).parent.parent / "scripts" / "backup_db.py"
    
    result = subprocess.run([
        sys.executable, str(script_path),
        "backup", str(live_db_path), str(backup_path),
    ], capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert backup_path.exists()

    # Wipe the original, then restore from the backup via the script's own CLI.
    live_db_path.unlink()
    result = subprocess.run([
        sys.executable, str(script_path),
        "restore", str(backup_path), str(live_db_path),
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

    # Verify the restored DB has the original row.
    conn = sqlite3.connect(live_db_path)
    cursor = conn.execute("SELECT val FROM test")
    row = cursor.fetchone()
    assert row[0] == "hello"
    conn.close()
