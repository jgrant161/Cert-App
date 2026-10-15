"""Storage. SQLite on one computer; PostgreSQL when DATABASE_URL points at one.

Queries use ``?`` placeholders (converted for PostgreSQL), inserts return their
id with ``RETURNING id``, and rows come back as plain dicts. Money is always
whole cents in INTEGER columns, never floats.
"""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS consignor (
    id {pk},
    code TEXT UNIQUE,                            -- C0001, printed on labels
    name TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    address TEXT,
    payout_method TEXT NOT NULL DEFAULT 'check', -- check | zelle | paypal | venmo | ach | cash | store_credit
    payout_details TEXT,                         -- Zelle phone, PayPal email, mailing name...
    commission_pct REAL,                         -- overrides the shop's tiers when set
    notes TEXT,
    agreement_accepted_at TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_user (
    id {pk},
    email TEXT NOT NULL UNIQUE,                  -- stored lower-case
    name TEXT NOT NULL,
    role TEXT NOT NULL,                          -- owner | staff | consignor
    consignor_id INTEGER REFERENCES consignor(id),
    password_hash TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    session_version INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_login_at TEXT
);
CREATE TABLE IF NOT EXISTS invite (
    id {pk},
    user_id INTEGER NOT NULL REFERENCES app_user(id),
    token_hash TEXT NOT NULL UNIQUE,
    expires_at TEXT NOT NULL,
    used_at TEXT
);
CREATE TABLE IF NOT EXISTS item (
    id {pk},
    sku TEXT UNIQUE,                             -- DO000042: the barcode, and the SKU in Square
    consignor_id INTEGER NOT NULL REFERENCES consignor(id),
    title TEXT NOT NULL,
    category TEXT,
    sport TEXT,                                  -- sport or game (Baseball, Pokemon...)
    year TEXT,
    brand_set TEXT,
    card_number TEXT,
    subject TEXT,                                -- player or character
    grader TEXT,                                 -- PSA, BGS, SGC, CGC... blank when raw
    grade TEXT,
    cert_number TEXT,
    condition TEXT,
    description TEXT,
    tcgplayer_id TEXT,
    quantity INTEGER NOT NULL DEFAULT 1,
    asking_cents INTEGER,                        -- what the consignor asked for at submission
    min_cents INTEGER,                           -- the consignor's floor
    price_cents INTEGER,                         -- the agreed list price
    commission_pct REAL,                         -- set for this item only; else consignor, else tiers
    status TEXT NOT NULL,                        -- submitted | received | listed | sold | returned | declined
    location TEXT,                               -- case / shelf
    submitted_by TEXT NOT NULL DEFAULT 'staff',  -- staff | consignor
    received_at TEXT,
    listed_at TEXT,
    expires_at TEXT,                             -- end of the consignment period
    sold_at TEXT,
    closed_at TEXT,
    square_item_id TEXT,
    square_variation_id TEXT,
    square_synced_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS item_consignor ON item (consignor_id, status);
CREATE TABLE IF NOT EXISTS photo (
    id {pk},
    item_id INTEGER NOT NULL REFERENCES item(id),
    token TEXT NOT NULL UNIQUE,                  -- unguessable; used in public photo links for listings
    ext TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    uploaded_by INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS price_proposal (
    id {pk},
    item_id INTEGER NOT NULL REFERENCES item(id),
    proposed_by TEXT NOT NULL,                   -- shop | consignor
    user_id INTEGER,
    price_cents INTEGER NOT NULL,
    note TEXT,
    status TEXT NOT NULL DEFAULT 'pending',      -- pending | accepted | declined | withdrawn
    created_at TEXT NOT NULL,
    decided_at TEXT
);
CREATE TABLE IF NOT EXISTS sale (
    id {pk},
    item_id INTEGER NOT NULL REFERENCES item(id),
    consignor_id INTEGER NOT NULL REFERENCES consignor(id),
    channel TEXT NOT NULL,                       -- instore | ebay | whatnot | tcgplayer | other
    sold_at TEXT NOT NULL,                       -- date, YYYY-MM-DD
    price_cents INTEGER NOT NULL,                -- what the buyer paid for the item (no tax or shipping)
    fees_cents INTEGER NOT NULL,                 -- marketplace and card-processing fees
    commission_pct REAL NOT NULL,
    commission_cents INTEGER NOT NULL,           -- the shop's share
    consignor_cents INTEGER NOT NULL,            -- what the consignor is owed
    external_ref TEXT,                           -- order number on the channel
    square_order_id TEXT,
    voided_at TEXT,
    void_reason TEXT,
    user_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS sale_square_line ON sale (square_order_id, item_id);
CREATE TABLE IF NOT EXISTS payout (
    id {pk},
    consignor_id INTEGER NOT NULL REFERENCES consignor(id),
    amount_cents INTEGER NOT NULL,
    method TEXT NOT NULL,
    reference TEXT,                              -- check number, Zelle confirmation...
    status TEXT NOT NULL,                        -- pending | sent | void
    memo TEXT,
    created_at TEXT NOT NULL,
    sent_at TEXT,
    user_id INTEGER
);
CREATE TABLE IF NOT EXISTS adjustment (
    id {pk},
    consignor_id INTEGER NOT NULL REFERENCES consignor(id),
    amount_cents INTEGER NOT NULL,               -- + owed to the consignor, - charged to them
    memo TEXT NOT NULL,
    at TEXT NOT NULL,
    user_id INTEGER
);
CREATE TABLE IF NOT EXISTS transfer (
    id {pk},
    direction TEXT NOT NULL,                     -- to_consignment | to_shop
    amount_cents INTEGER NOT NULL,
    at TEXT NOT NULL,                            -- date, YYYY-MM-DD
    reference TEXT,
    memo TEXT,
    user_id INTEGER,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS setting (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id {pk},
    at TEXT NOT NULL,
    user_id INTEGER,
    user_email TEXT,
    action TEXT NOT NULL,
    consignor_id INTEGER,
    item_id INTEGER,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log (at);
"""


def data_dir() -> Path:
    path = Path(os.environ.get("CONSIGN_DATA_DIR", "data"))
    path.mkdir(parents=True, exist_ok=True)
    return path


def files_dir() -> Path:
    path = Path(os.environ.get("CONSIGN_FILES_DIR") or data_dir() / "files")
    path.mkdir(parents=True, exist_ok=True)
    return path


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


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


@contextmanager
def connect():
    if is_postgres():
        import psycopg
        from psycopg.rows import dict_row
        raw = psycopg.connect(_database_url(), row_factory=dict_row)
        conn = Conn(raw, True)
    else:
        raw = sqlite3.connect(data_dir() / "consign.db", timeout=30)
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


def _update(table: str, row_id: int, fields: dict, stamp: bool = False) -> None:
    if stamp:
        fields = {**fields, "updated_at": now()}
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with connect() as conn:
        conn.execute(f"UPDATE {table} SET {cols} WHERE id = ?", [*fields.values(), row_id])


# --- settings --------------------------------------------------------------

def get_setting(key: str, default=None):
    with connect() as conn:
        row = conn.one("SELECT value FROM setting WHERE key = ?", (key,))
    return json.loads(row["value"]) if row else default


def set_setting(key: str, value) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM setting WHERE key = ?", (key,))
        conn.execute("INSERT INTO setting (key, value) VALUES (?, ?)", (key, json.dumps(value)))


# --- consignors ------------------------------------------------------------

CONSIGNOR_FIELDS = ("name", "email", "phone", "address", "payout_method", "payout_details",
                    "commission_pct", "notes", "active")


def create_consignor(name: str, **fields) -> int:
    fields = {k: v for k, v in fields.items() if k in CONSIGNOR_FIELDS}
    cols = ["name", "created_at", *fields]
    with connect() as conn:
        cid = conn.insert(f"INSERT INTO consignor ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          [name, now(), *fields.values()])
        conn.execute("UPDATE consignor SET code = ? WHERE id = ?", (f"C{cid:04d}", cid))
    return cid


def get_consignor(cid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM consignor WHERE id = ?", (cid,))


def list_consignors(active_only: bool = False) -> list[dict]:
    where = "WHERE c.active = 1" if active_only else ""
    with connect() as conn:
        return conn.all(f"""
            SELECT c.*, COALESCE(x.on_hand, 0) AS on_hand, COALESCE(x.submitted, 0) AS submitted
            FROM consignor c LEFT JOIN (
                SELECT consignor_id,
                       SUM(CASE WHEN status IN ('received', 'listed') THEN 1 ELSE 0 END) AS on_hand,
                       SUM(CASE WHEN status = 'submitted' THEN 1 ELSE 0 END) AS submitted
                FROM item GROUP BY consignor_id) x ON x.consignor_id = c.id
            {where} ORDER BY c.name
        """)


def update_consignor(cid: int, **fields) -> None:
    _update("consignor", cid, {k: v for k, v in fields.items() if k in CONSIGNOR_FIELDS + ("agreement_accepted_at",)})


# --- users -----------------------------------------------------------------

def count_users() -> int:
    with connect() as conn:
        return conn.one("SELECT COUNT(*) AS n FROM app_user")["n"]


def create_user(email: str, name: str, role: str, password_hash: str | None = None,
                consignor_id: int | None = None) -> int:
    with connect() as conn:
        return conn.insert(
            "INSERT INTO app_user (email, name, role, consignor_id, password_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (email.strip().lower(), name.strip(), role, consignor_id, password_hash, now()))


def get_user(uid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM app_user WHERE id = ?", (uid,))


def get_user_by_email(email: str) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM app_user WHERE email = ?", (email.strip().lower(),))


def list_users(consignor_id: int | None = None, staff_only: bool = False) -> list[dict]:
    with connect() as conn:
        if consignor_id is not None:
            return conn.all("SELECT * FROM app_user WHERE consignor_id = ? ORDER BY name", (consignor_id,))
        if staff_only:
            return conn.all("SELECT * FROM app_user WHERE role IN ('owner', 'staff') ORDER BY name")
        return conn.all("SELECT * FROM app_user ORDER BY name")


def update_user(uid: int, **fields) -> None:
    allowed = {"name", "email", "role", "password_hash", "active", "last_login_at"}
    _update("app_user", uid, {k: v for k, v in fields.items() if k in allowed})


def bump_session_version(uid: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE app_user SET session_version = session_version + 1 WHERE id = ?", (uid,))


def create_invite(uid: int, token_hash: str, expires_at: str) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO invite (user_id, token_hash, expires_at) VALUES (?, ?, ?)",
                     (uid, token_hash, expires_at))


def get_invite(token_hash: str) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM invite WHERE token_hash = ?", (token_hash,))


def use_invite(invite_id: int) -> None:
    with connect() as conn:
        conn.execute("UPDATE invite SET used_at = ? WHERE id = ?", (now(), invite_id))


# --- items -----------------------------------------------------------------

ITEM_FIELDS = ("consignor_id", "title", "category", "sport", "year", "brand_set", "card_number", "subject",
               "grader", "grade", "cert_number", "condition", "description", "tcgplayer_id", "quantity",
               "asking_cents", "min_cents", "price_cents", "commission_pct", "status", "location",
               "submitted_by", "received_at", "listed_at", "expires_at", "sold_at", "closed_at",
               "square_item_id", "square_variation_id", "square_synced_at")


def create_item(**fields) -> int:
    fields = {k: v for k, v in fields.items() if k in ITEM_FIELDS}
    stamp = now()
    cols = [*fields, "created_at", "updated_at"]
    with connect() as conn:
        iid = conn.insert(f"INSERT INTO item ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                          [*fields.values(), stamp, stamp])
        conn.execute("UPDATE item SET sku = ? WHERE id = ?", (f"DO{iid:06d}", iid))
    return iid


def get_item(iid: int) -> dict | None:
    with connect() as conn:
        return conn.one("""SELECT i.*, c.name AS consignor_name, c.code AS consignor_code,
                                  c.commission_pct AS consignor_pct
                           FROM item i JOIN consignor c ON c.id = i.consignor_id WHERE i.id = ?""", (iid,))


def get_item_by_sku(sku: str) -> dict | None:
    with connect() as conn:
        row = conn.one("SELECT id FROM item WHERE sku = ?", (sku.strip().upper(),))
    return get_item(row["id"]) if row else None


def get_item_by_square_variation(vid: str) -> dict | None:
    with connect() as conn:
        row = conn.one("SELECT id FROM item WHERE square_variation_id = ?", (vid,))
    return get_item(row["id"]) if row else None


def list_items(consignor_id: int | None = None, status: str | None = None, q: str | None = None,
               ids: list[int] | None = None, limit: int = 1000) -> list[dict]:
    where, args = [], []
    if consignor_id is not None:
        where.append("i.consignor_id = ?")
        args.append(consignor_id)
    if status == "on_hand":
        where.append("i.status IN ('received', 'listed')")
    elif status == "expired":
        where.append("i.status IN ('received', 'listed') AND i.expires_at IS NOT NULL AND i.expires_at < ?")
        args.append(today())
    elif status:
        where.append("i.status = ?")
        args.append(status)
    if q:
        like = f"%{q.strip().lower()}%"
        where.append("(LOWER(i.title) LIKE ? OR LOWER(i.sku) LIKE ? OR LOWER(COALESCE(i.cert_number, '')) LIKE ?"
                     " OR LOWER(c.name) LIKE ?)")
        args += [like, like, like, like]
    if ids is not None:
        if not ids:
            return []
        where.append(f"i.id IN ({','.join('?' * len(ids))})")
        args += ids
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with connect() as conn:
        return conn.all(f"""
            SELECT i.*, c.name AS consignor_name, c.code AS consignor_code, c.commission_pct AS consignor_pct,
                   (SELECT p.token || '.' || p.ext FROM photo p WHERE p.item_id = i.id
                    ORDER BY p.position, p.id LIMIT 1) AS cover
            FROM item i JOIN consignor c ON c.id = i.consignor_id
            {clause} ORDER BY i.updated_at DESC, i.id DESC LIMIT {int(limit)}
        """, args)


def update_item(iid: int, **fields) -> None:
    _update("item", iid, {k: v for k, v in fields.items() if k in ITEM_FIELDS}, stamp=True)


def item_counts(consignor_id: int | None = None) -> dict[str, int]:
    where, args = ("WHERE consignor_id = ?", [consignor_id]) if consignor_id is not None else ("", [])
    with connect() as conn:
        rows = conn.all(f"SELECT status, COUNT(*) AS n FROM item {where} GROUP BY status", args)
    return {r["status"]: r["n"] for r in rows}


# --- photos ----------------------------------------------------------------

def add_photo(iid: int, token: str, ext: str, uid: int | None) -> int:
    with connect() as conn:
        pos = conn.one("SELECT COALESCE(MAX(position), -1) + 1 AS p FROM photo WHERE item_id = ?", (iid,))["p"]
        return conn.insert("INSERT INTO photo (item_id, token, ext, position, uploaded_by, created_at)"
                           " VALUES (?, ?, ?, ?, ?, ?)", (iid, token, ext, pos, uid, now()))


def list_photos(iid: int) -> list[dict]:
    with connect() as conn:
        return conn.all("SELECT * FROM photo WHERE item_id = ? ORDER BY position, id", (iid,))


def photos_for_items(ids: list[int]) -> dict[int, list[dict]]:
    if not ids:
        return {}
    with connect() as conn:
        rows = conn.all(f"SELECT * FROM photo WHERE item_id IN ({','.join('?' * len(ids))}) ORDER BY position, id",
                        ids)
    out: dict[int, list[dict]] = {}
    for r in rows:
        out.setdefault(r["item_id"], []).append(r)
    return out


def get_photo_by_token(token: str) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM photo WHERE token = ?", (token,))


def get_photo(pid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM photo WHERE id = ?", (pid,))


def delete_photo(pid: int) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM photo WHERE id = ?", (pid,))


def make_cover(pid: int) -> None:
    p = get_photo(pid)
    with connect() as conn:
        conn.execute("UPDATE photo SET position = position + 1 WHERE item_id = ?", (p["item_id"],))
        conn.execute("UPDATE photo SET position = 0 WHERE id = ?", (pid,))


# --- price proposals -------------------------------------------------------

def add_proposal(iid: int, by: str, uid: int | None, price_cents: int, note: str | None) -> int:
    with connect() as conn:
        conn.execute("UPDATE price_proposal SET status = 'withdrawn', decided_at = ? "
                     "WHERE item_id = ? AND status = 'pending'", (now(), iid))
        return conn.insert("INSERT INTO price_proposal (item_id, proposed_by, user_id, price_cents, note, created_at)"
                           " VALUES (?, ?, ?, ?, ?, ?)", (iid, by, uid, price_cents, note, now()))


def list_proposals(iid: int) -> list[dict]:
    with connect() as conn:
        return conn.all("SELECT * FROM price_proposal WHERE item_id = ? ORDER BY id DESC", (iid,))


def pending_proposal(iid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM price_proposal WHERE item_id = ? AND status = 'pending'", (iid,))


def decide_proposal(pid: int, status: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE price_proposal SET status = ?, decided_at = ? WHERE id = ?", (status, now(), pid))


def pending_proposals(by: str, consignor_id: int | None = None) -> list[dict]:
    where, args = "p.status = 'pending' AND p.proposed_by = ?", [by]
    if consignor_id is not None:
        where += " AND i.consignor_id = ?"
        args.append(consignor_id)
    with connect() as conn:
        return conn.all(f"""SELECT p.*, i.title, i.sku, i.price_cents AS current_cents, c.name AS consignor_name
                            FROM price_proposal p JOIN item i ON i.id = p.item_id
                            JOIN consignor c ON c.id = i.consignor_id
                            WHERE {where} ORDER BY p.id""", args)


# --- sales -----------------------------------------------------------------

def add_sale(**f) -> int:
    cols = ["item_id", "consignor_id", "channel", "sold_at", "price_cents", "fees_cents", "commission_pct",
            "commission_cents", "consignor_cents", "external_ref", "square_order_id", "user_id"]
    with connect() as conn:
        return conn.insert(f"INSERT INTO sale ({', '.join(cols)}, created_at) VALUES ({', '.join('?' * len(cols))}, ?)",
                           [f.get(c) for c in cols] + [now()])


def get_sale(sid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM sale WHERE id = ?", (sid,))


def list_sales(consignor_id: int | None = None, item_id: int | None = None, since: str | None = None,
               include_void: bool = True, limit: int = 5000) -> list[dict]:
    where, args = [], []
    if consignor_id is not None:
        where.append("s.consignor_id = ?")
        args.append(consignor_id)
    if item_id is not None:
        where.append("s.item_id = ?")
        args.append(item_id)
    if since:
        where.append("s.sold_at >= ?")
        args.append(since)
    if not include_void:
        where.append("s.voided_at IS NULL")
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with connect() as conn:
        return conn.all(f"""SELECT s.*, i.title, i.sku, c.name AS consignor_name FROM sale s
                            JOIN item i ON i.id = s.item_id JOIN consignor c ON c.id = s.consignor_id
                            {clause} ORDER BY s.sold_at DESC, s.id DESC LIMIT {int(limit)}""", args)


def void_sale(sid: int, reason: str) -> None:
    with connect() as conn:
        conn.execute("UPDATE sale SET voided_at = ?, void_reason = ? WHERE id = ?", (now(), reason, sid))


def square_order_seen(order_id: str, item_id: int) -> bool:
    with connect() as conn:
        return conn.one("SELECT 1 AS x FROM sale WHERE square_order_id = ? AND item_id = ?",
                        (order_id, item_id)) is not None


# --- payouts, adjustments, transfers ---------------------------------------

def add_payout(consignor_id: int, amount_cents: int, method: str, reference: str | None, status: str,
               memo: str | None, uid: int | None) -> int:
    with connect() as conn:
        return conn.insert(
            "INSERT INTO payout (consignor_id, amount_cents, method, reference, status, memo, created_at, sent_at, user_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (consignor_id, amount_cents, method, reference, status, memo, now(),
             today() if status == "sent" else None, uid))


def get_payout(pid: int) -> dict | None:
    with connect() as conn:
        return conn.one("SELECT * FROM payout WHERE id = ?", (pid,))


def update_payout(pid: int, **fields) -> None:
    _update("payout", pid, {k: v for k, v in fields.items() if k in {"status", "reference", "sent_at", "memo"}})


def list_payouts(consignor_id: int | None = None, status: str | None = None) -> list[dict]:
    where, args = [], []
    if consignor_id is not None:
        where.append("p.consignor_id = ?")
        args.append(consignor_id)
    if status:
        where.append("p.status = ?")
        args.append(status)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    with connect() as conn:
        return conn.all(f"""SELECT p.*, c.name AS consignor_name FROM payout p JOIN consignor c ON c.id = p.consignor_id
                            {clause} ORDER BY p.id DESC""", args)


def add_adjustment(consignor_id: int, amount_cents: int, memo: str, at: str, uid: int | None) -> int:
    with connect() as conn:
        return conn.insert("INSERT INTO adjustment (consignor_id, amount_cents, memo, at, user_id) VALUES (?, ?, ?, ?, ?)",
                           (consignor_id, amount_cents, memo, at, uid))


def list_adjustments(consignor_id: int | None = None) -> list[dict]:
    where, args = ("WHERE a.consignor_id = ?", [consignor_id]) if consignor_id is not None else ("", [])
    with connect() as conn:
        return conn.all(f"""SELECT a.*, c.name AS consignor_name FROM adjustment a JOIN consignor c ON c.id = a.consignor_id
                            {where} ORDER BY a.at DESC, a.id DESC""", args)


def add_transfer(direction: str, amount_cents: int, at: str, reference: str | None, memo: str | None,
                 uid: int | None) -> int:
    with connect() as conn:
        return conn.insert("INSERT INTO transfer (direction, amount_cents, at, reference, memo, user_id, created_at)"
                           " VALUES (?, ?, ?, ?, ?, ?, ?)", (direction, amount_cents, at, reference, memo, uid, now()))


def list_transfers() -> list[dict]:
    with connect() as conn:
        return conn.all("SELECT * FROM transfer ORDER BY at DESC, id DESC")


# --- audit log -------------------------------------------------------------

def audit(user: dict | None, action: str, consignor_id: int | None = None, item_id: int | None = None,
          detail: str | None = None) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit_log (at, user_id, user_email, action, consignor_id, item_id, detail)"
                     " VALUES (?, ?, ?, ?, ?, ?, ?)",
                     (now(), user["id"] if user else None, user["email"] if user else None, action,
                      consignor_id, item_id, detail))


def list_audit(limit: int = 300, item_id: int | None = None) -> list[dict]:
    where, args = ("WHERE item_id = ?", [item_id]) if item_id is not None else ("", [])
    with connect() as conn:
        return conn.all(f"SELECT * FROM audit_log {where} ORDER BY id DESC LIMIT {int(limit)}", args)
