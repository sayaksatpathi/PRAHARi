import pytest
from prahari.edge.auth import LoginThrottle, UserStore
from prahari.common.db import Database

def test_jwt_rbac_auth(tmp_path):
    db = Database(str(tmp_path / "auth.db"))
    store = UserStore(db)

    admin_pw = store.ensure_bootstrap_admin()

    # login
    principal = store.authenticate("admin", admin_pw)
    assert principal is not None
    assert principal.role == "admin"

    # invalid login
    assert store.authenticate("admin", "wrongpassword") is None


# --------------------------------------------------------------------------
# Brute-force protection (LoginThrottle)
# --------------------------------------------------------------------------

def test_throttle_locks_after_max_failures():
    t = LoginThrottle(max_failures=3, window_s=300, base_lockout_s=30)
    key = "admin|10.0.0.5"
    assert t.retry_after(key) == 0.0
    assert t.record_failure(key) == 0.0      # 1
    assert t.record_failure(key) == 0.0      # 2
    locked = t.record_failure(key)           # 3 -> lockout
    assert locked == 30
    assert t.retry_after(key) > 0            # further attempts refused


def test_throttle_success_clears_the_counter():
    t = LoginThrottle(max_failures=3, base_lockout_s=30)
    key = "admin|10.0.0.5"
    t.record_failure(key)
    t.record_failure(key)
    t.record_success(key)                    # good login resets
    assert t.retry_after(key) == 0.0
    assert t.record_failure(key) == 0.0      # count started over


def test_throttle_lockout_escalates():
    t = LoginThrottle(max_failures=1, base_lockout_s=30, max_lockout_s=900)
    key = "admin|10.0.0.5"
    assert t.record_failure(key) == 30       # 1st lockout
    assert t.record_failure(key) == 60       # doubles
    assert t.record_failure(key) == 120


def test_throttle_is_per_key():
    """An attacker on one IP must not lock out an operator on another."""
    t = LoginThrottle(max_failures=1, base_lockout_s=30)
    t.record_failure("admin|1.1.1.1")
    assert t.retry_after("admin|2.2.2.2") == 0.0   # different source unaffected
