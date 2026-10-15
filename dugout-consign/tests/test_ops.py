import pytest
from conftest import add_item

from consign import db, ops


@pytest.fixture
def cid():
    return db.create_consignor("Jen")


def test_life_of_an_item(cid, owner):
    iid = db.create_item(consignor_id=cid, title="Charizard", status="submitted", submitted_by="consignor")
    item = db.get_item(iid)
    assert item["sku"] == f"DO{iid:06d}"
    with pytest.raises(ops.OpError):
        ops.list_item(item, owner)
    ops.receive(item, owner, "Case 3")
    item = db.get_item(iid)
    assert item["status"] == "received" and item["expires_at"] > db.today() and item["location"] == "Case 3"
    with pytest.raises(ops.OpError):
        ops.list_item(item, owner)          # no price yet
    ops.set_price(item, 5000, owner)
    ops.list_item(db.get_item(iid), owner)
    assert db.get_item(iid)["status"] == "listed"
    ops.close_item(db.get_item(iid), "returned", owner)
    assert db.get_item(iid)["status"] == "returned"
    ops.relist(db.get_item(iid), owner)
    assert db.get_item(iid)["status"] == "received"


def test_floor_is_enforced(cid, owner):
    item = add_item(cid, price=10000, min_cents=9000)
    with pytest.raises(ops.OpError, match="floor"):
        ops.record_sale(item, "instore", 8000, None, db.today(), owner)
    ops.record_sale(item, "instore", 8000, None, db.today(), owner, allow_below_floor=True)


def test_quantity_counts_down(cid, owner):
    item = add_item(cid, price=5000, quantity=2)
    ops.record_sale(item, "whatnot", 5000, None, db.today(), owner)
    item = db.get_item(item["id"])
    assert (item["quantity"], item["status"]) == (1, "listed")
    ops.record_sale(item, "whatnot", 5000, None, db.today(), owner)
    item = db.get_item(item["id"])
    assert (item["quantity"], item["status"]) == (0, "sold")
    with pytest.raises(ops.OpError):
        ops.record_sale(item, "instore", 5000, None, db.today(), owner)


def test_estimated_fee_used_when_blank(cid, owner):
    sid, _ = ops.record_sale(add_item(cid, price=10000), "ebay", 10000, None, db.today(), owner)
    assert db.get_sale(sid)["fees_cents"] == 1365


def test_price_proposals_both_ways(cid, owner):
    item = add_item(cid, price=10000, min_cents=9000)
    ops.propose_price(item, "consignor", 12000, "comps are up", None)
    p = db.pending_proposal(item["id"])
    ops.answer_proposal(item, p, True, owner)
    assert db.get_item(item["id"])["price_cents"] == 12000
    ops.propose_price(db.get_item(item["id"]), "shop", 8500, None, owner)
    # a newer proposal replaces any older pending one
    ops.propose_price(db.get_item(item["id"]), "shop", 8000, None, owner)
    assert [p["status"] for p in db.list_proposals(item["id"])] == ["pending", "withdrawn", "accepted"]
    ops.answer_proposal(db.get_item(item["id"]), db.pending_proposal(item["id"]), True, None)
    item = db.get_item(item["id"])
    assert item["price_cents"] == 8000 and item["min_cents"] == 8000   # consignor agreed below their floor
    with pytest.raises(ops.OpError):
        ops.answer_proposal(item, db.list_proposals(item["id"])[0], False, None)


def test_square_failure_is_a_warning_not_a_lost_sale(cid, owner):
    item = add_item(cid, price=5000, square_item_id="X", square_variation_id="V")

    class Broken:
        def set_stock(self, *a):
            raise RuntimeError("offline")
    sid, warning = ops.record_sale(item, "ebay", 5000, None, db.today(), owner, sq=Broken())
    assert db.get_sale(sid) and "offline" in warning
