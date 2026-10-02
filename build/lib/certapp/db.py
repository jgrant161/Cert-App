"""Storage. SQLite on a single computer; PostgreSQL when DATABASE_URL points at one.

The same SQL runs on both: queries are written with ``?`` placeholders and
converted for PostgreSQL, inserts return their id with ``RETURNING id``, and
rows always come back as plain dicts.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS engagement (
    id {pk},
    client_name TEXT NOT NULL,
    seller_name TEXT,
    policy_json TEXT NOT NULL DEFAULT '{{}}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch (
    id {pk},
    engagement_id INTEGER NOT NULL REFERENCES engagement(id),
    label TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    files_received INTEGER NOT NULL DEFAULT 0,
    files_new INTEGER NOT NULL DEFAULT 0,
    files_duplicate INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS certificate (
    id {pk},
    engagement_id INTEGER NOT NULL REFERENCES engagement(id),
    batch_id INTEGER NOT NULL REFERENCES batch(id),
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    filename_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | processing | extracted | error
    extractor TEXT,
    extraction_json TEXT,
    error TEXT,
    duplicate_of INTEGER REFERENCES certificate(id),
    review_status TEXT NOT NULL DEFAULT 'open',  -- open | reviewed | follow_up
    reviewer_note TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS certificate_hash ON certificate (engagement_id, sha256);
CREATE TABLE IF NOT EXISTS line_override (
    id {pk},
    certificate_id INTEGER NOT NULL REFERENCES certificate(id),
    state TEXT NOT NULL,
    decision TEXT NOT NULL,                      -- include | exclude
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (certificate_id, state)
);
CREATE TABLE IF NOT EXISTS qa (
    id {pk},
    engagement_id INTEGER NOT NULL REFERENCES engagement(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_user (
    id {pk},
    email TEXT NOT NULL UNIQUE,                  -- stored lower-case
    name TEXT NOT NULL,
    role TEXT NOT NULL,                          -- firm_admin | firm_staff | client_admin | client_user
    engagement_id INTEGER REFERENCES engagement(id),   -- the client a client user belongs to
    auth_method TEXT NOT NULL,                   -- password | microsoft
    password_hash TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 1,  -- bump to sign the user out everywhere
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS engagement_staff (
    engagement_id INTEGER NOT NULL REFERENCES engagement(id),
    user_id INTEGER NOT NULL REFERENCES app_user(id),
    PRIMARY KEY (engagement_id, user_id)
);
CREATE TABLE IF NOT EXISTS invite (
    id {pk},
    user_id INTEGER NOT NULL REFERENCES app_user(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS audit_log (
    id {pk},
    at TEXT NOT NULL,
    user_id INTEGER,
    user_email TEXT,
    action TEXT NOT NULL,
    engagement_id INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log (at);
"""


def data_dir() -> Path:
    path = Path(os.environ.get("CERTAPP_DATA_DIR", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_dir() -> Path:
    """Where uploaded PDFs live. In the cloud, point this at mounted persistent storage."""
    path = Path(os.environ.get("CERTAPP_FILES_DIR") or data_dir() / "files")
    path.mkdir(parents=True, exist_ok=True)
    return path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _database_url() -> str:
    return os.environ.get("DATABASE_URL", "")


def is_postgres() -> bool:
    return _database_url().startswith(("postgres://", "postgresql://"))


class Conn:
    """A thin wrapper so callers write one dialect of SQL."""

    def __init__(self, raw, postgres: bool):
        self.raw = raw
        self.postgres = postgres

    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.postgres else sql

    def execute(self, sql: str, args=()):
        return self.raw.execute(self._sql(sql), tuple(args))

    def all(self, sql: str, args=()) -> list[dict]:
        return [dict(r) for r in self.execute(sql, args).fetchall()]

    def one(self, sql: str, args=()) -> dict | None:
        row = self.execute(sql, args).fetchone()
        return dict(row) if row is not None else None

    def insert(self, sql: str, args=()) -> int:
        return self.one(sql + " RETURNING id", args)["id"]

    def executemany(self, sql: str, rows) -> None:
        if self.postgres:
            with self.raw.cursor() as cur:
                cur.executemany(self._sql(sql), rows)
        else:
            self.raw.executemany(sql, rows)


@contextmanager
def connect():
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(_database_url(), row_factory=dict_row)
        conn = Conn(raw, True)
    else:
        raw = sqlite3.connect(data_dir() / "certapp.db", timeout=30)
        raw.row_factory = sqlite3.Row
        raw.execute("PRAGMA foreign_keys = ON")
        conn = Conn(raw, False)
    try:
        yield conn
        raw.commit()
    except BaseException:
        raw.rollback()
        raise
    finally:
        raw.close()


def init() -> None:
    with connect() as conn:
        if conn.postgres:
            conn.raw.execute(SCHEMA.format(pk="INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY"))
        else:
            conn.raw.execute("PRAGMA journal_mode = WAL")
            conn.raw.executescript(SCHEMA.format(pk="INTEGER PRIMARY KEY"))


def ping() -> bool:
    with connect() as conn:
        return conn.one("SELECT 1 AS ok")["ok"] == 1


# --- engagements -----------------------------------------------------------

def create_engagement(client_name: str, seller_name: str | None = None) -> int:
    with connect() as conn:
        return conn.insert(
            "INSERT INTO engagement (client_name, seller_name, created_at) VALUES (?, ?, ?)",
            (client_name, seller_name, now()))


def list_engagements(ids: list[int] | None = None) -> list[dict]:
    """All engagements, or only those in ``ids`` (an empty list returns none)."""
    where, args = "", []
    if ids is not None:
        if not ids:
            return []
        where = f"WHERE e.id IN ({','.join('?' * len(ids))})"
        args = list(ids)
    with connect() as conn:
        return conn.all(f"""
            SELECT e.id, e.client_name, e.seller_name, e.created_at, COUNT(c.id) AS cert_count,
                   SUM(CASE WHEN c.status IN ('pending', 'processing') THEN 1 ELSE 0 END) AS pending_count
            FROM engagement e LEFT JOIN certificate c ON c.engagement_id = e.id
            {where}
            GROUP BY e.id, e.client_name, e.seller_name, e.created_at ORDER BY e.created_at DESC
        """, args)


def get_engagement(eid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM engagement WHERE id = ?", (eid,))


def delete_engagement(eid: int) -> None:
    """Remove an engagement, its client users, and everything under it (files are removed by the caller)."""
    with connect() as conn:
        conn.execute("""DELETE FROM line_override WHERE certificate_id IN
                        (SELECT id FROM certificate WHERE engagement_id = ?)""", (eid,))
        conn.execute("UPDATE certificate SET duplicate_of = NULL WHERE engagement_id = ?", (eid,))
        for table in ("qa", "certificate", "batch", "engagement_staff"):
            conn.execute(f"DELETE FROM {table} WHERE engagement_id = ?", (eid,))
        conn.execute("DELETE FROM invite WHERE user_id IN (SELECT id FROM app_user WHERE engagement_id = ?)", (eid,))
        conn.execute("DELETE FROM app_user WHERE engagement_id = ?", (eid,))
        conn.execute("DELETE FROM engagement WHERE id = ?", (eid,))


def save_policy(eid: int, policy: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE engagement SET policy_json = ? WHERE id = ?", (json.dumps(policy), eid))


# --- batches & certificates ------------------------------------------------

def create_batch(eid: int, label: str) -> int:
    with connect() as conn:
        return conn.insert("INSERT INTO batch (engagement_id, label, uploaded_at) VALUES (?, ?, ?)",
                           (eid, label, now()))


def update_batch_counts(bid: int, received: int, new: int, dup: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE batch SET files_received = ?, files_new = ?, files_duplicate = ? WHERE id = ?",
            (received, new, dup, bid))


def list_batches(eid: int) -> list[dict]:
    with connect() as conn:
        return conn.all("SELECT * FROM batch WHERE engagement_id = ? ORDER BY id", (eid,))


def get_batch(bid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM batch WHERE id = ?", (bid,))


def find_by_hash(eid: int, sha: str) -> list[dict]:
    with connect() as conn:
        return conn.all("SELECT * FROM certificate WHERE engagement_id = ? AND sha256 = ? ORDER BY id",
                        (eid, sha))


def add_certificate(eid: int, bid: int, filename: str, sha: str, path: str,
                    filename_info: dict, duplicate_of: int | None = None) -> int:
    """Byte-identical copies under a new name are kept (so every source file is
    accounted for) but point at the original and are never re-read."""
    status = "extracted" if duplicate_of else "pending"
    with connect() as conn:
        return conn.insert(
            """INSERT INTO certificate (engagement_id, batch_id, filename, sha256, storage_path,
                                        filename_json, status, duplicate_of, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, bid, filename, sha, path, json.dumps(filename_info), status, duplicate_of, now()))


def list_certificates(eid: int, status: str | None = None) -> list[dict]:
    sql = """SELECT c.*, b.label AS batch_label FROM certificate c
             JOIN batch b ON b.id = c.batch_id WHERE c.engagement_id = ?"""
    args: list = [eid]
    if status:
        sql += " AND c.status = ?"
        args.append(status)
    with connect() as conn:
        return conn.all(sql + " ORDER BY lower(c.filename), c.id", args)


def get_certificate(cid: int) -> dict | None:
    with connect() as conn:
        return conn.one("""SELECT c.*, b.label AS batch_label FROM certificate c
                           JOIN batch b ON b.id = c.batch_id WHERE c.id = ?""", (cid,))


def claim_pending(eid: int) -> list[dict]:
    """Mark every pending certificate as processing and return them."""
    with connect() as conn:
        rows = conn.all("SELECT * FROM certificate WHERE engagement_id = ? AND status = 'pending'", (eid,))
        conn.executemany("UPDATE certificate SET status = 'processing' WHERE id = ?",
                         [(r["id"],) for r in rows])
        return rows


def reset_interrupted() -> int:
    """Certificates left 'processing' by a shutdown mid-read go back to pending."""
    with connect() as conn:
        return conn.execute("UPDATE certificate SET status = 'pending' WHERE status = 'processing'").rowcount


def save_extraction(cid: int, extractor: str, extraction_json: str) -> None:
    with connect() as conn:
        conn.execute("""UPDATE certificate SET status = 'extracted', extractor = ?, extraction_json = ?,
                        error = NULL WHERE id = ?""", (extractor, extraction_json, cid))


def save_error(cid: int, error: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE certificate SET status = 'error', error = ? WHERE id = ?", (error, cid))


def requeue(cid: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE certificate SET status = 'pending', error = NULL WHERE id = ?", (cid,))


def set_review(cid: int, review_status: str, note: str | None) -> None:
    with connect() as conn:
        conn.execute("UPDATE certificate SET review_status = ?, reviewer_note = ? WHERE id = ?",
                     (review_status, note, cid))


# --- overrides -------------------------------------------------------------

def set_override(cid: int, state: str, decision: str | None, note: str | None = None) -> None:
    with connect() as conn:
        if decision is None:
            conn.execute("DELETE FROM line_override WHERE certificate_id = ? AND state = ?", (cid, state))
        else:
            conn.execute(
                """INSERT INTO line_override (certificate_id, state, decision, note, created_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT (certificate_id, state)
                   DO UPDATE SET decision = excluded.decision, note = excluded.note""",
                (cid, state, decision, note, now()))


def overrides_for_engagement(eid: int) -> dict[int, dict[str, dict]]:
    with connect() as conn:
        rows = conn.all("""SELECT o.* FROM line_override o JOIN certificate c ON c.id = o.certificate_id
                           WHERE c.engagement_id = ?""", (eid,))
    out: dict[int, dict[str, dict]] = {}
    for r in rows:
        out.setdefault(r["certificate_id"], {})[r["state"]] = r
    return out


# --- Q&A -------------------------------------------------------------------

def add_qa(eid: int, question: str, answer: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO qa (engagement_id, question, answer, created_at) VALUES (?, ?, ?, ?)",
                     (eid, question, answer, now()))


def list_qa(eid: int) -> list[dict]:
    with connect() as conn:
        return conn.all("SELECT * FROM qa WHERE engagement_id = ? ORDER BY id", (eid,))


# --- users -----------------------------------------------------------------

def count_users() -> int:
    with connect() as conn:
        return conn.one("SELECT COUNT(*) AS n FROM app_user")["n"]


def create_user(email: str, name: str, role: str, auth_method: str,
                engagement_id: int | None = None, password_hash: str | None = None) -> int:
    with connect() as conn:
        return conn.insert(
            """INSERT INTO app_user (email, name, role, engagement_id, auth_method, password_hash, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (email.strip().lower(), name.strip(), role, engagement_id, auth_method, password_hash, now()))


def get_user(uid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM app_user WHERE id = ?", (uid,))


def get_user_by_email(email: str) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM app_user WHERE email = ?", (email.strip().lower(),))


def list_users(engagement_id: int | None = None) -> list[dict]:
    sql = """SELECT u.*, e.client_name FROM app_user u LEFT JOIN engagement e ON e.id = u.engagement_id"""
    args: list = []
    if engagement_id is not None:
        sql += " WHERE u.engagement_id = ?"
        args.append(engagement_id)
    with connect() as conn:
        return conn.all(sql + " ORDER BY u.role, lower(u.name)", args)


def update_user(uid: int, **fields) -> None:
    allowed = {"name", "role", "engagement_id", "auth_method", "password_hash", "active", "last_login_at"}
    keys = [k for k in fields if k in allowed]
    if not keys:
        return
    with connect() as conn:
        conn.execute(f"UPDATE app_user SET {', '.join(k + ' = ?' for k in keys)} WHERE id = ?",
                     [fields[k] for k in keys] + [uid])


def bump_session_version(uid: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE app_user SET session_version = session_version + 1 WHERE id = ?", (uid,))


def staff_engagement_ids(uid: int) -> list[int]:
    with connect() as conn:
        return [r["engagement_id"] for r in
                conn.all("SELECT engagement_id FROM engagement_staff WHERE user_id = ?", (uid,))]


def engagement_staff(eid: int) -> list[dict]:
    with connect() as conn:
        return conn.all("""SELECT u.* FROM engagement_staff s JOIN app_user u ON u.id = s.user_id
                           WHERE s.engagement_id = ? ORDER BY lower(u.name)""", (eid,))


def set_staff_engagements(uid: int, engagement_ids: list[int]) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM engagement_staff WHERE user_id = ?", (uid,))
        conn.executemany("INSERT INTO engagement_staff (engagement_id, user_id) VALUES (?, ?)",
                         [(eid, uid) for eid in sorted(set(engagement_ids))])


def assign_staff(eid: int, uid: int) -> None:
    with connect() as conn:
        conn.execute("""INSERT INTO engagement_staff (engagement_id, user_id) VALUES (?, ?)
                        ON CONFLICT (engagement_id, user_id) DO NOTHING""", (eid, uid))


# --- invitations -----------------------------------------------------------

def create_invite(uid: int, token_hash: str, expires_at: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE invite SET used_at = ? WHERE user_id = ? AND used_at IS NULL", (now(), uid))
        conn.execute("INSERT INTO invite (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                     (uid, token_hash, expires_at))


def get_invite(token_hash: str) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM invite WHERE token_hash = ?", (token_hash,))


def use_invite(invite_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE invite SET used_at = ? WHERE id = ?", (now(), invite_id))


# --- audit log -------------------------------------------------------------

def audit(user: dict | None, action: str, engagement_id: int | None = None, detail: str | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO audit_log (at, user_id, user_email, action, engagement_id, detail) VALUES (?, ?, ?, ?, ?, ?)",
            (now(), user["id"] if user else None, user["email"] if user else None, action, engagement_id, detail))


def list_audit(limit: int = 300, engagement_ids: list[int] | None = None) -> list[dict]:
    where, args = "", []
    if engagement_ids is not None:
        if not engagement_ids:
            return []
        where = f"WHERE a.engagement_id IN ({','.join('?' * len(engagement_ids))})"
        args = list(engagement_ids)
    with connect() as conn:
        return conn.all(f"""SELECT a.*, e.client_name FROM audit_log a
                            LEFT JOIN engagement e ON e.id = a.engagement_id
                            {where} ORDER BY a.id DESC LIMIT {int(limit)}""", args)
