"""SQLite storage. One file per installation; every table is scoped by engagement."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS engagement (
    id INTEGER PRIMARY KEY,
    client_name TEXT NOT NULL,
    seller_name TEXT,
    policy_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS batch (
    id INTEGER PRIMARY KEY,
    engagement_id INTEGER NOT NULL REFERENCES engagement(id),
    label TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    files_received INTEGER NOT NULL DEFAULT 0,
    files_new INTEGER NOT NULL DEFAULT 0,
    files_duplicate INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS certificate (
    id INTEGER PRIMARY KEY,
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
    id INTEGER PRIMARY KEY,
    certificate_id INTEGER NOT NULL REFERENCES certificate(id),
    state TEXT NOT NULL,
    decision TEXT NOT NULL,                      -- include | exclude
    note TEXT,
    created_at TEXT NOT NULL,
    UNIQUE (certificate_id, state)
);
CREATE TABLE IF NOT EXISTS qa (
    id INTEGER PRIMARY KEY,
    engagement_id INTEGER NOT NULL REFERENCES engagement(id),
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def data_dir() -> Path:
    path = Path(os.environ.get("CERTAPP_DATA_DIR", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect():
    conn = sqlite3.connect(data_dir() / "certapp.db", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init() -> None:
    with connect() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


# --- engagements -----------------------------------------------------------

def create_engagement(client_name: str, seller_name: str | None = None) -> int:
    with connect() as conn:
        cur = conn.execute(
            "INSERT INTO engagement (client_name, seller_name, created_at) VALUES (?, ?, ?)",
            (client_name, seller_name, now()),
        )
        return cur.lastrowid


def list_engagements() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("""
            SELECT e.*, COUNT(c.id) AS cert_count,
                   SUM(c.status IN ('pending', 'processing')) AS pending_count
            FROM engagement e LEFT JOIN certificate c ON c.engagement_id = e.id
            GROUP BY e.id ORDER BY e.created_at DESC
        """).fetchall()


def get_engagement(eid: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute("SELECT * FROM engagement WHERE id = ?", (eid,)).fetchone()


def save_policy(eid: int, policy: dict) -> None:
    with connect() as conn:
        conn.execute("UPDATE engagement SET policy_json = ? WHERE id = ?", (json.dumps(policy), eid))


# --- batches & certificates ------------------------------------------------

def create_batch(eid: int, label: str) -> int:
    with connect() as conn:
        return conn.execute(
            "INSERT INTO batch (engagement_id, label, uploaded_at) VALUES (?, ?, ?)",
            (eid, label, now()),
        ).lastrowid


def update_batch_counts(bid: int, received: int, new: int, dup: int) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE batch SET files_received = ?, files_new = ?, files_duplicate = ? WHERE id = ?",
            (received, new, dup, bid),
        )


def list_batches(eid: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM batch WHERE engagement_id = ? ORDER BY id", (eid,)
        ).fetchall()


def find_by_hash(eid: int, sha: str) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT * FROM certificate WHERE engagement_id = ? AND sha256 = ? ORDER BY id", (eid, sha)
        ).fetchall()


def add_certificate(eid: int, bid: int, filename: str, sha: str, path: str,
                    filename_info: dict, duplicate_of: int | None = None) -> int:
    """Byte-identical copies under a new name are kept (so every source file is
    accounted for) but point at the original and are never re-read."""
    status = "extracted" if duplicate_of else "pending"
    with connect() as conn:
        return conn.execute(
            """INSERT INTO certificate (engagement_id, batch_id, filename, sha256, storage_path,
                                        filename_json, status, duplicate_of, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, bid, filename, sha, path, json.dumps(filename_info), status, duplicate_of, now()),
        ).lastrowid


def list_certificates(eid: int, status: str | None = None) -> list[sqlite3.Row]:
    sql = """SELECT c.*, b.label AS batch_label FROM certificate c
             JOIN batch b ON b.id = c.batch_id WHERE c.engagement_id = ?"""
    args: list = [eid]
    if status:
        sql += " AND c.status = ?"
        args.append(status)
    with connect() as conn:
        return conn.execute(sql + " ORDER BY c.filename COLLATE NOCASE", args).fetchall()


def get_certificate(cid: int) -> sqlite3.Row | None:
    with connect() as conn:
        return conn.execute(
            """SELECT c.*, b.label AS batch_label FROM certificate c
               JOIN batch b ON b.id = c.batch_id WHERE c.id = ?""", (cid,)
        ).fetchone()


def claim_pending(eid: int) -> list[sqlite3.Row]:
    """Mark every pending certificate as processing and return them."""
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM certificate WHERE engagement_id = ? AND status = 'pending'", (eid,)
        ).fetchall()
        conn.executemany("UPDATE certificate SET status = 'processing' WHERE id = ?",
                         [(r["id"],) for r in rows])
        return rows


def save_extraction(cid: int, extractor: str, extraction_json: str) -> None:
    with connect() as conn:
        conn.execute(
            """UPDATE certificate SET status = 'extracted', extractor = ?, extraction_json = ?,
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


def overrides_for_engagement(eid: int) -> dict[int, dict[str, sqlite3.Row]]:
    with connect() as conn:
        rows = conn.execute(
            """SELECT o.* FROM line_override o JOIN certificate c ON c.id = o.certificate_id
               WHERE c.engagement_id = ?""", (eid,)).fetchall()
    out: dict[int, dict[str, sqlite3.Row]] = {}
    for r in rows:
        out.setdefault(r["certificate_id"], {})[r["state"]] = r
    return out


# --- Q&A -------------------------------------------------------------------

def add_qa(eid: int, question: str, answer: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO qa (engagement_id, question, answer, created_at) VALUES (?, ?, ?, ?)",
                     (eid, question, answer, now()))


def list_qa(eid: int) -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute("SELECT * FROM qa WHERE engagement_id = ? ORDER BY id", (eid,)).fetchall()
