"""Balances, statements and the two-account reconciliation.

The shop keeps two bank accounts:

* **Shop operating account**: where sales land (Square deposits, eBay and
  Whatnot payouts) and where the shop's commission stays.
* **Consignment trust account**: holds only money owed to consignors. Payouts
  are made from it.

What each consignor is owed (their *balance*) is every sale's consignor share,
plus or minus adjustments, less payouts sent. The trust account should hold
exactly the total of those balances. Because sales land in the shop account,
the gap between the two is the transfer the shop owes the trust account, shown
on the dashboard until it is moved and recorded.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import db
from .money import setting


def _hold_cutoff() -> str:
    return (date.fromisoformat(db.today()) - timedelta(days=int(setting("hold_days")))).isoformat()


def consignor_balance(cid: int) -> dict:
    sales = db.list_sales(consignor_id=cid, include_void=False)
    adjustments = db.list_adjustments(cid)
    payouts = db.list_payouts(cid)
    cutoff = _hold_cutoff()
    earned = sum(s["consignor_cents"] for s in sales)
    held = sum(s["consignor_cents"] for s in sales if s["sold_at"] > cutoff)
    adjusted = sum(a["amount_cents"] for a in adjustments)
    sent = sum(p["amount_cents"] for p in payouts if p["status"] == "sent")
    pending = sum(p["amount_cents"] for p in payouts if p["status"] == "pending")
    balance = earned + adjusted - sent
    return {
        "earned": earned,                      # lifetime consignor share of sales
        "adjusted": adjusted,
        "paid": sent,
        "pending_payouts": pending,
        "balance": balance,                    # owed now, including held money
        "held": held,                          # inside the hold period
        "available": balance - held - pending, # can be paid out today
        "sales_count": len(sales),
        "gross": sum(s["price_cents"] for s in sales),
    }


def statement(cid: int) -> list[dict]:
    """Every money event for a consignor, oldest first, with a running balance."""
    rows = []
    for s in db.list_sales(consignor_id=cid, include_void=False):
        rows.append({"date": s["sold_at"], "kind": "sale", "item_id": s["item_id"], "sku": s["sku"],
                     "description": s["title"], "channel": s["channel"], "price": s["price_cents"],
                     "fees": s["fees_cents"], "commission": s["commission_cents"], "pct": s["commission_pct"],
                     "amount": s["consignor_cents"], "sort": (s["sold_at"], 0, s["id"])})
    for a in db.list_adjustments(cid):
        rows.append({"date": a["at"], "kind": "adjustment", "description": a["memo"], "amount": a["amount_cents"],
                     "sort": (a["at"], 1, a["id"])})
    for p in db.list_payouts(cid, status="sent"):
        rows.append({"date": p["sent_at"] or p["created_at"][:10], "kind": "payout",
                     "description": f"Payout by {p['method'].replace('_', ' ')}"
                                    + (f" ({p['reference']})" if p["reference"] else ""),
                     "amount": -p["amount_cents"], "sort": (p["sent_at"] or p["created_at"][:10], 2, p["id"])})
    rows.sort(key=lambda r: r["sort"])
    running = 0
    for r in rows:
        running += r["amount"]
        r["balance"] = running
    return rows


def accounts() -> dict:
    """Where the money is, and what the trust account should hold."""
    consignors = db.list_consignors()
    balances = {c["id"]: consignor_balance(c["id"]) for c in consignors}
    owed = sum(b["balance"] for b in balances.values())
    transfers = db.list_transfers()
    moved_in = sum(t["amount_cents"] for t in transfers if t["direction"] == "to_consignment")
    moved_out = sum(t["amount_cents"] for t in transfers if t["direction"] == "to_shop")
    paid = sum(b["paid"] for b in balances.values())
    trust = int(setting("opening_consignment_cents")) + moved_in - moved_out - paid
    sales = db.list_sales(include_void=False)
    return {
        "owed": owed,                                  # total owed to all consignors
        "trust_balance": trust,                        # what the trust account should show at the bank
        "transfer_due": owed - trust,                  # + move shop -> trust, - trust holds extra
        "available": sum(max(b["available"], 0) for b in balances.values()),
        "held": sum(b["held"] for b in balances.values()),
        "commission": sum(s["commission_cents"] for s in sales),
        "fees": sum(s["fees_cents"] for s in sales),
        "gross": sum(s["price_cents"] for s in sales),
        "moved_in": moved_in,
        "moved_out": moved_out,
        "paid": paid,
        "balances": balances,
    }


def period_summary(since: str) -> dict:
    sales = db.list_sales(since=since, include_void=False)
    by_channel: dict[str, dict] = {}
    for s in sales:
        c = by_channel.setdefault(s["channel"], {"count": 0, "gross": 0, "fees": 0, "commission": 0, "consignor": 0})
        c["count"] += 1
        c["gross"] += s["price_cents"]
        c["fees"] += s["fees_cents"]
        c["commission"] += s["commission_cents"]
        c["consignor"] += s["consignor_cents"]
    return {
        "count": len(sales),
        "gross": sum(s["price_cents"] for s in sales),
        "commission": sum(s["commission_cents"] for s in sales),
        "consignor": sum(s["consignor_cents"] for s in sales),
        "fees": sum(s["fees_cents"] for s in sales),
        "by_channel": by_channel,
    }
