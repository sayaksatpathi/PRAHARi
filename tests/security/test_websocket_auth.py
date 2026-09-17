import pytest
from fastapi.testclient import TestClient
from prahari.edge.app import app
from prahari.edge.auth import issue_token, Principal

@pytest.fixture
def test_client():
    return TestClient(app)

def test_websocket_rejects_unauthenticated(test_client):
    from fastapi.websockets import WebSocketDisconnect
    try:
        with test_client.websocket_connect("/ws") as ws:
            ws.receive_text()
        assert False, "Should have been rejected"
    except WebSocketDisconnect as e:
        assert e.code == 1008

def test_websocket_accepts_authenticated(test_client):
    from prahari.edge.app import runtime
    token = issue_token(
        username="test", role="viewer",
        secret=runtime.settings.secret_key, ttl_seconds=3600
    )
    with test_client.websocket_connect(f"/ws?token={token}") as ws:
        data = ws.receive_json()
        assert data["subject"] == "system.status"
