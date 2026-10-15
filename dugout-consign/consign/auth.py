"""Who can sign in, and what each role may do.

owner      Everything, including settings, staff accounts, voiding sales and payouts.
staff      Day-to-day work: consignors, intake, pricing, labels, sales, payouts, Square, listings.
consignor  Their own account only: items, prices, sales, statement, payouts, item submissions.

Consignor separation is enforced in one place: every consignor page loads
records through ``own_item`` / the signed-in user's ``consignor_id``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from . import db

ROLES = {"owner": "Owner", "staff": "Staff", "consignor": "Consignor"}
STAFF_ROLES = {"owner", "staff"}
OWNER_ONLY = {"settings", "users", "void"}

MIN_PASSWORD_LENGTH = 10
INVITE_DAYS = 14


def is_staff(user: dict | None) -> bool:
    return bool(user) and user["role"] in STAFF_ROLES


def can(user: dict | None, action: str) -> bool:
    if not user:
        return False
    if action in OWNER_ONLY:
        return user["role"] == "owner"
    return user["role"] in STAFF_ROLES


# --- passwords -------------------------------------------------------------

_SCRYPT = {"n": 2 ** 15, "r": 8, "p": 1, "maxmem": 64 * 1024 * 1024, "dklen": 32}


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    key = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(key).decode()


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt_b64, key_b64 = stored.split("$")
    key = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt_b64), **_SCRYPT)
    return hmac.compare_digest(key, base64.b64decode(key_b64))


def password_problem(password: str, confirm: str | None = None) -> str | None:
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if confirm is not None and password != confirm:
        return "The two passwords do not match."
    return None


class LoginThrottle:
    """Lock an email out for a while after repeated wrong passwords."""

    def __init__(self, max_failures: int = 5, window_seconds: int = 15 * 60):
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str) -> list[float]:
        cutoff = time.monotonic() - self.window
        recent = [t for t in self._failures.get(key, []) if t > cutoff]
        self._failures[key] = recent
        return recent

    def locked(self, email: str) -> bool:
        with self._lock:
            return len(self._recent(email.lower())) >= self.max_failures

    def failed(self, email: str) -> None:
        with self._lock:
            self._recent(email.lower()).append(time.monotonic())

    def succeeded(self, email: str) -> None:
        with self._lock:
            self._failures.pop(email.lower(), None)


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


def authenticate(email: str, password: str, throttle: LoginThrottle) -> tuple[dict | None, str | None]:
    generic = "That email and password do not match an active account."
    if throttle.locked(email):
        return None, "Too many attempts. Wait 15 minutes, then try again."
    user = db.get_user_by_email(email)
    if not user or not user["active"] or not verify_password(password, user["password_hash"]):
        if not user:
            verify_password(password, _DUMMY_HASH)   # same work either way, so timing gives nothing away
        throttle.failed(email)
        return None, generic
    throttle.succeeded(email)
    return user, None


def start_session(session: dict, user: dict) -> None:
    session.clear()
    session["uid"] = user["id"]
    session["sv"] = user["session_version"]
    db.update_user(user["id"], last_login_at=db.now())


def current_user(session: dict) -> dict | None:
    uid = session.get("uid")
    if not uid:
        return None
    user = db.get_user(uid)
    if not user or not user["active"] or user["session_version"] != session.get("sv"):
        session.clear()
        return None
    return user


# --- invitations -----------------------------------------------------------

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_invite_token(uid: int) -> str:
    """A one-time link token for setting a password. Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=INVITE_DAYS)).isoformat(timespec="seconds")
    db.create_invite(uid, _hash_token(token), expires)
    return token


def check_invite_token(token: str) -> tuple[dict | None, dict | None]:
    invite = db.get_invite(_hash_token(token))
    if not invite or invite["used_at"] or invite["expires_at"] < db.now():
        return None, None
    user = db.get_user(invite["user_id"])
    if not user or not user["active"]:
        return None, None
    return invite, user
