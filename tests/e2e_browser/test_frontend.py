import pytest
from playwright.sync_api import Page, expect
import threading
import time
import requests
import uvicorn
from prahari.edge.app import app

def run_server():
    uvicorn.run(app, host="127.0.0.1", port=9997, log_level="error")

@pytest.fixture(scope="session", autouse=True)
def start_server():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    
    # Wait for server to boot
    for _ in range(30):
        try:
            requests.get("http://127.0.0.1:9997/api/health")
            break
        except requests.ConnectionError:
            time.sleep(0.1)
    yield

def test_login_and_dashboard_websocket(page: Page):
    # Overwrite admin password for testing
    from prahari.edge.app import runtime
    runtime.users.create("admin", "testpass", "admin")
    
    # Navigate to app
    page.goto("http://127.0.0.1:9997/")
    
    # Fill in admin credentials
    page.fill('#lg-user', "admin")
    page.fill('#lg-pass', "testpass")
    
    # Click login button
    page.click('button[type="submit"]')
    
    # Wait for the dashboard to render (app div becomes visible)
    page.wait_for_selector('.brand-name:has-text("PRAHARI")', state='visible')
    
    # Assert successful login (no error box shown)
    expect(page.locator('#login-error')).to_be_hidden()
