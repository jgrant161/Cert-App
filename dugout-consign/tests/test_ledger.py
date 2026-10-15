from datetime import date, timedelta

import pytest
from conftest import add_item

from consign import db, ledger, money, ops


def ago(n):
    return (date.fromisoformat(db.today()) - timedelta(days=n)).isoformat()


@pytest.fixture
def cid():
    return db.create_consignor("Mike", commission_pct=20.0)


def test_sale_owed_held_then_available(cid, owner):
    item = add_item(cid, price=10000)
    ops.record_sale(item, "instore", 10000, 0, ago(1), owner)
    b = ledger.consignor_balance(cid)
    assert b["balance"] == 8000 and b["held"] == 8000 and b["available"] == 0
    old = add_item(cid, price=5000)
    ops.record_sale(old, "ebay", 5000, 500, ago(30), owner)
    b = ledger.consignor_balance(cid)
    assert b["balance"] == 8000 + 3600 and b["available"] == 3600


def test_payouts_and_adjustments(cid, owner):
    ops.record_sale(add_item(cid, price=10000), "instore", 10000, 0, ago(30), owner)
    db.add_adjustment(cid, -1500, "PSA grading", db.today(), owner["id"])
    c = db.get_consignor(cid)
    with pytest.raises(ops.OpError):
        ops.create_payout(c, 7000, "zelle", None, True, None, owner, ledger.consignor_balance(cid)["available"])
    pid = ops.create_payout(c, 4000, "zelle", None, False, None, owner, ledger.consignor_balance(cid)["available"])
    b = ledger.consignor_balance(cid)
    assert (b["balance"], b["pending_payouts"], b["available"]) == (6500, 4000, 2500)
    ops.mark_payout_sent(db.get_payout(pid), "ZL1", owner)
    b = ledger.consignor_balance(cid)
    assert (b["balance"], b["paid"], b["available"]) == (2500, 4000, 2500)
    rows = ledger.statement(cid)
    assert [r["kind"] for r in rows] == ["sale", "adjustment", "payout"]
    assert rows[-1]["balance"] == 2500


def test_trust_account_reconciliation(cid, owner):
    ops.record_sale(add_item(cid, price=10000), "instore", 10000, 0, ago(30), owner)
    a = ledger.accounts()
    assert a["owed"] == 8000 and a["trust_balance"] == 0 and a["transfer_due"] == 8000
    db.add_transfer("to_consignment", 8000, db.today(), None, None, owner["id"])
    assert ledger.accounts()["transfer_due"] == 0
    ops.create_payout(db.get_consignor(cid), 8000, "check", "1001", True, None, owner, 8000)
    a = ledger.accounts()
    assert (a["owed"], a["trust_balance"], a["transfer_due"]) == (0, 0, 0)
    assert a["commission"] == 2000


def test_opening_balance_counts(cid):
    db.set_setting("opening_consignment_cents", 2500)
    assert ledger.accounts()["trust_balance"] == 2500
    assert ledger.accounts()["transfer_due"] == -2500


def test_void_sale_reverses_everything(cid, owner):
    item = add_item(cid, price=10000)
    sid, _ = ops.record_sale(item, "instore", 10000, 0, ago(30), owner)
    assert db.get_item(item["id"])["status"] == "sold"
    with pytest.raises(ops.OpError):
        ops.void_sale(db.get_sale(sid), " ", owner)
    ops.void_sale(db.get_sale(sid), "Returned", owner)
    assert db.get_item(item["id"])["status"] == "listed"
    assert ledger.consignor_balance(cid)["balance"] == 0
    assert money.fmt(ledger.accounts()["owed"]) == "$0.00"
