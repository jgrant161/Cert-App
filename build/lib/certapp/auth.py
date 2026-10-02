"""Who can sign in, and what each role may do.

Roles
-----
firm_admin    Everything: all clients, users, staff assignments, the activity log.
firm_staff    Works the clients they are assigned to: upload, review, policy, export, questions.
client_admin  Their own company only: read the schedule and follow-ups, export, manage their users.
client_user   Their own company only: read the schedule and follow-ups, export.

Client separation is enforced in one place: ``can_access_engagement``. Every
route that touches client data calls ``require_engagement`` first.
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

ROLES = {
    "firm_admin": "Firm administrator",
    "firm_staff": "Firm staff",
    "client_admin": "Client administrator",
    "client_user": "Client user",
}
FIRM_ROLES = {"firm_admin", "firm_staff"}
CLIENT_ROLES = {"client_admin", "client_user"}

# Actions on a client's data, by role. Anything not listed is refused.
PERMISSIONS = {
    "firm_admin": {"view", "export", "work", "ask", "policy", "delete", "create_engagement",
                   "manage_users", "manage_client_users", "view_audit"},
    "firm_staff": {"view", "export", "work", "ask", "policy", "view_audit"},
    "client_admin": {"view", "export", "manage_client_users"},
    "client_user": {"view", "export"},
}

MIN_PASSWORD_LENGTH = 12
INVITE_DAYS = 7


def can(user: dict | None, action: str) -> bool:
    return bool(user) and action in PERMISSIONS.get(user["role"], set())


def accessible_engagement_ids(user: dict) -> list[int] | None:
    """None means every engagement (firm admins); otherwise the explicit list."""
    if user["role"] == "firm_admin":
        return None
    if user["role"] == "firm_staff":
        return db.staff_engagement_ids(user["id"])
    return [user["engagement_id"]] if user.get("engagement_id") else []


def can_access_engagement(user: dict | None, eid: int) -> bool:
    if not user:
        return False
    ids = accessible_engagement_ids(user)
    return ids is None or eid in ids


def can_manage_user(actor: dict, target: dict) -> bool:
    """Firm admins manage everyone; client admins manage users of their own company."""
    if can(actor, "manage_users"):
        return True
    return (can(actor, "manage_client_users") and target["role"] in CLIENT_ROLES
            and target.get("engagement_id") == actor.get("engagement_id"))


def assignable_roles(actor: dict) -> list[str]:
    if can(actor, "manage_users"):
        return list(ROLES)
    if can(actor, "manage_client_users"):
        return ["client_admin", "client_user"]
    return []


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


# --- sign-in attempts ------------------------------------------------------

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


def authenticate(email: str, password: str, throttle: LoginThrottle) -> tuple[dict | None, str | None]:
    """Return (user, None) on success or (None, message) on failure."""
    generic = "That email and password do not match an active account."
    if throttle.locked(email):
        return None, "Too many attempts. Wait 15 minutes, then try again."
    user = db.get_user_by_email(email)
    if user and user["active"] and user["auth_method"] == "microsoft":
        return None, "This account signs in with Microsoft. Use the Microsoft button."
    if not user or not user["active"] or not verify_password(password, user["password_hash"]):
        if not user:
            verify_password(password, _DUMMY_HASH)  # same work either way, so timing gives nothing away
        throttle.failed(email)
        return None, generic
    throttle.succeeded(email)
    return user, None


_DUMMY_HASH = hash_password(secrets.token_urlsafe(16))


# --- sessions --------------------------------------------------------------

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
    """Return (invite, user) if the token is valid and unused, else (None, None)."""
    invite = db.get_invite(_hash_token(token))
    if not invite or invite["used_at"] or invite["expires_at"] < db.now():
        return None, None
    user = db.get_user(invite["user_id"])
    if not user or not user["active"]:
        return None, None
    return invite, user
