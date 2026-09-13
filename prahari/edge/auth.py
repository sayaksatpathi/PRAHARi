"""Authentication and role-based access control.

Deliberately built on the standard library rather than a JWT dependency: the
token format here is a signed, expiring bearer token, which is all the node
needs, and adding a library to produce the same thing would be a dependency on
an edge box for no capability.

Password hashing uses scrypt with a per-user salt. Roles are three:

    ADMIN     - configure cameras, zones and retention; everything below
    OPERATOR  - acknowledge alerts, submit true/false-alarm feedback, view
    VIEWER    - read only

The first-run administrator password is generated randomly and printed once to
the console. It is never committed, never defaulted to something guessable, and
never stored in plaintext. A deployment that wants a known password must set one
explicitly.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Literal

from prahari.common.db import Database

log = logging.getLogger("prahari.auth")

Role = Literal["admin", "operator", "viewer"]

ROLE_RANK: dict[str, int] = {"viewer": 0, "operator": 1, "admin": 2}

SCRYPT_N = 2 ** 14
SCRYPT_R = 8
SCRYPT_P = 1


@dataclass
class Principal:
    username: str
    role: str

    def may(self, required: Role) -> bool:
        return ROLE_RANK.get(self.role, -1) >= ROLE_RANK[required]


def hash_password(password: str, salt: bytes | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt,
                            n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return base64.b64encode(salt).decode(), base64.b64encode(digest).decode()


def verify_password(password: str, salt_b64: str, hash_b64: str) -> bool:
    try:
        salt = base64.b64decode(salt_b64)
    except Exception:
        return False
    _, computed = hash_password(password, salt)
    # Constant-time: a timing oracle on the admin password of a border
    # surveillance node is not a theoretical concern.
    return hmac.compare_digest(computed, hash_b64)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _unb64url(text: str) -> bytes:
    pad = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + pad)


def issue_token(username: str, role: str, secret: str, ttl_seconds: int) -> str:
    payload = {"sub": username, "role": role,
               "exp": int(time.time()) + ttl_seconds,
               "jti": secrets.token_hex(8)}
    body = _b64url(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64url(sig)}"


def verify_token(token: str, secret: str) -> Principal | None:
    try:
        body, sig = token.split(".", 1)
    except ValueError:
        return None
    expected = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_unb64url(sig), expected):
            return None
        payload = json.loads(_unb64url(body))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return Principal(username=payload.get("sub", ""), role=payload.get("role", "viewer"))


class UserStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(self, username: str, password: str, role: Role) -> None:
        salt, pw_hash = hash_password(password)
        from datetime import datetime, timezone

        self.db.execute(
            "INSERT OR REPLACE INTO users(username, role, salt, pw_hash, created_at) "
            "VALUES(?,?,?,?,?)",
            (username, role, salt, pw_hash,
             datetime.now(timezone.utc).isoformat()),
        )

    def authenticate(self, username: str, password: str) -> Principal | None:
        row = self.db.query_one(
            "SELECT username, role, salt, pw_hash FROM users WHERE username=?",
            (username,),
        )
        if not row:
            # Burn comparable time on an unknown user so the response does not
            # reveal which usernames exist.
            hash_password(password)
            return None
        if not verify_password(password, row["salt"], row["pw_hash"]):
            return None
        return Principal(username=row["username"], role=row["role"])

    def count(self) -> int:
        row = self.db.query_one("SELECT COUNT(*) AS c FROM users")
        return int(row["c"]) if row else 0

    def ensure_bootstrap_admin(self) -> str | None:
        """Create the first admin if there is none. Returns the password once."""
        if self.count() > 0:
            return None
        password = secrets.token_urlsafe(12)
        self.create("admin", password, "admin")
        self.db.audit("system", "user.bootstrap", "admin",
                      "initial administrator created with a generated password")
        return password
