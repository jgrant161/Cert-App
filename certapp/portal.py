"""Vendor certificate portal: a client's vendors ask whether a sale is exempt.

The vendor picks the buying entity's project and lists the items. Each item
is matched to the client's rules (set up by the firm), and the answer is one
of three: exempt (here is the certificate), taxable (charge tax), or review
(contact the client). The rules decide; nothing is guessed. An item no rule
covers, or an exemption with no certificate on file, goes to review.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import date

from . import db

PROJECT_TYPES = {
    "manufacturing": "Manufacturing or production facility",
    "resale": "Inventory for resale or distribution",
    "construction": "Construction or real property improvement",
    "administrative": "Offices and general business use",
    "exempt_owner": "Project for an exempt owner (government, nonprofit)",
}
CERT_TYPES = {
    "resale": "Resale certificate",
    "manufacturing": "Manufacturing exemption certificate",
    "direct_pay": "Direct pay permit",
    "exempt_project": "Exempt project certificate",
    "other": "Other exemption certificate",
}
OUTCOMES = {"exempt": "Exempt", "taxable": "Taxable", "review": "Needs review"}

TABLES = {"entity": "portal_entity", "project": "portal_project", "category": "portal_category",
          "certificate": "portal_certificate", "rule": "portal_rule"}


# --- storage ---------------------------------------------------------------

def add(kind: str, eid: int, **values) -> int:
    table = TABLES[kind]
    cols = ["engagement_id", *values]
    with db.connect() as conn:
        return conn.insert(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                           [eid, *values.values()])


def remove(kind: str, eid: int, item_id: int) -> dict | None:
    """Delete one setup row of this client. Returns the row, or None if it is not this client's."""
    table = TABLES[kind]
    with db.connect() as conn:
        row = conn.one(f"SELECT * FROM {table} WHERE id = ? AND engagement_id = ?", (item_id, eid))
        if row:
            if kind == "entity":
                conn.execute("DELETE FROM portal_certificate WHERE entity_id = ?", (item_id,))
                conn.execute("DELETE FROM portal_project WHERE entity_id = ?", (item_id,))
            if kind == "category":
                conn.execute("DELETE FROM portal_rule WHERE category_id = ?", (item_id,))
            conn.execute(f"DELETE FROM {table} WHERE id = ?", (item_id,))
        return row


def listing(kind: str, eid: int) -> list[dict]:
    order = {"entity": "name", "project": "name", "category": "name",
             "certificate": "state, cert_type", "rule": "id"}[kind]
    with db.connect() as conn:
        return conn.all(f"SELECT * FROM {TABLES[kind]} WHERE engagement_id = ? ORDER BY {order}", (eid,))


def get(kind: str, eid: int, item_id: int) -> dict | None:
    with db.connect() as conn:
        return conn.one(f"SELECT * FROM {TABLES[kind]} WHERE id = ? AND engagement_id = ?", (item_id, eid))


def contact(eid: int) -> dict:
    with db.connect() as conn:
        return conn.one("SELECT * FROM portal_contact WHERE engagement_id = ?", (eid,)) or {}


def save_contact(eid: int, name: str, email: str, phone: str, message: str) -> None:
    with db.connect() as conn:
        conn.execute("""INSERT INTO portal_contact (engagement_id, name, email, phone, message) VALUES (?, ?, ?, ?, ?)
                        ON CONFLICT (engagement_id) DO UPDATE SET name = excluded.name, email = excluded.email,
                        phone = excluded.phone, message = excluded.message""",
                     (eid, name, email, phone, message))


def record_request(eid: int, user: dict, project_id: int, reference: str, items: list[dict],
                   result: "Decision") -> int:
    with db.connect() as conn:
        return conn.insert(
            """INSERT INTO portal_request (engagement_id, user_id, user_email, project_id, reference,
                                           items_json, result_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, user["id"], user["email"], project_id, reference, json.dumps(items),
             json.dumps(result.as_dict()), db.now()))


def requests(eid: int, user_id: int | None = None, request_id: int | None = None,
             limit: int = 200) -> list[dict]:
    sql = """SELECT r.*, p.name AS project_name, p.state AS project_state FROM portal_request r
             LEFT JOIN portal_project p ON p.id = r.project_id WHERE r.engagement_id = ?"""
    args: list = [eid]
    if user_id is not None:
        sql += " AND r.user_id = ?"
        args.append(user_id)
    if request_id is not None:
        sql += " AND r.id = ?"
        args.append(request_id)
    with db.connect() as conn:
        rows = conn.all(sql + f" ORDER BY r.id DESC LIMIT {int(limit)}", args)
    for r in rows:
        r["items"] = json.loads(r["items_json"])
        r["result"] = json.loads(r["result_json"])
    return rows


def get_request(eid: int, rid: int) -> dict | None:
    rows = requests(eid, request_id=rid, limit=1)
    return rows[0] if rows else None


# --- the decision ----------------------------------------------------------

@dataclass
class ItemDecision:
    description: str
    category: str
    outcome: str                  # exempt | taxable | review
    reason: str
    certificate_id: int | None = None
    certificate_label: str | None = None


@dataclass
class Decision:
    items: list[ItemDecision]
    certificates: list[dict] = field(default_factory=list)   # {id, label, state, cert_type}

    @property
    def status(self) -> str:
        outcomes = {i.outcome for i in self.items}
        if outcomes == {"exempt"}:
            return "exempt"
        if "review" in outcomes:
            return "review"
        return "taxable" if outcomes == {"taxable"} else "mixed"

    def as_dict(self) -> dict:
        return {"status": self.status, "items": [asdict(i) for i in self.items],
                "certificates": self.certificates}


def _rule_for(rules: list[dict], category_id: int, state: str, project_type: str) -> dict | None:
    """Most specific matching rule: an exact state beats *, then an exact project type beats *."""
    best, best_score = None, -1
    for r in rules:
        if r["category_id"] != category_id:
            continue
        if r["state"] not in (state, "*") or r["project_type"] not in (project_type, "*"):
            continue
        score = (2 if r["state"] == state else 0) + (1 if r["project_type"] == project_type else 0)
        if score > best_score:
            best, best_score = r, score
    return best


def _certificate_for(certs: list[dict], entity_id: int, state: str, cert_type: str, today: date) -> dict | None:
    usable = [c for c in certs
              if c["entity_id"] == entity_id and c["cert_type"] == cert_type and c["state"] in (state, "*")
              and not (c.get("expires") and c["expires"] < today.isoformat())]
    usable.sort(key=lambda c: c["state"] != state)          # the state's own certificate first
    return usable[0] if usable else None


def decide(project: dict, items: list[dict], categories: dict[int, dict], rules: list[dict],
           certs: list[dict], today: date | None = None) -> Decision:
    """items: [{"description": str, "category_id": int}]"""
    today = today or date.today()
    decided: list[ItemDecision] = []
    handed: dict[int, dict] = {}
    for item in items:
        cat = categories.get(item["category_id"])
        name = cat["name"] if cat else "Unknown category"
        rule = _rule_for(rules, item["category_id"], project["state"], project["project_type"]) if cat else None
        if rule is None:
            decided.append(ItemDecision(item["description"], name, "review",
                                        "No rule covers this item for this project yet."))
            continue
        note = rule.get("note") or ""
        if rule["outcome"] == "exempt":
            cert = _certificate_for(certs, project["entity_id"], project["state"], rule["cert_type"] or "", today)
            if cert is None:
                decided.append(ItemDecision(item["description"], name, "review",
                                            "Exempt, but no current certificate is on file for this entity and state."))
                continue
            handed[cert["id"]] = {"id": cert["id"], "label": cert["label"], "state": cert["state"],
                                  "cert_type": cert["cert_type"]}
            decided.append(ItemDecision(item["description"], name, "exempt", note or "Exempt purchase.",
                                        cert["id"], cert["label"]))
        elif rule["outcome"] == "taxable":
            decided.append(ItemDecision(item["description"], name, "taxable", note or "Charge sales tax."))
        else:
            decided.append(ItemDecision(item["description"], name, "review", note or "Needs a closer look."))
    return Decision(decided, list(handed.values()))
