import pytest
from unittest.mock import patch, MagicMock
from prahari.edge.sync import SyncManager

def test_mtls_sync_manager_client_config(tmp_path):
    import sqlite3
    from prahari.common.db import Database
    db = Database(":memory:")
    
    cert_path = str(tmp_path / "client.pem")
    with open(cert_path, "w") as f:
        f.write("MOCK_CERT")
        
    sm = SyncManager(
        db=db,
        core_url="https://core.local",
        node_id="test_node",
        core_token="mock_token",
        core_cert=cert_path,
        core_verify=False
    )
    
    with patch("httpx.AsyncClient") as MockClient:
        # Just call _client to see what args are passed
        client = sm._client(timeout=15.0)
        MockClient.assert_called_once_with(
            timeout=15.0,
            headers={"Authorization": "Bearer mock_token"},
            cert=cert_path,
            verify=False
        )
