import pytest
from prahari.edge.auth import UserStore
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
