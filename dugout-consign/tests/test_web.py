import re

import pytest
from conftest import PASSWORD, PNG, add_item, make_user, sign_in

from consign import db, ledger, ops


@pytest.fixture
def shop(make_client, owner):
    return sign_in(make_client(), owner)


def test_first_run_setup(make_client):
    c = make_client()
    assert c.get("/", follow_redirects=False).headers["location"] == "/setup"
    r = c.post("/setup", data={"name": "Harrison", "email": "Owner@DugoutTX.com", "password": PASSWORD,
                               "confirm": PASSWORD})
    assert r.status_code == 200 and "Settings" in r.text
    assert db.get_user_by_email("owner@dugouttx.com")["role"] == "owner"
    assert c.get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_every_staff_page_renders(shop, owner):
    cid = db.create_consignor("Mike", email="mike@example.com")
    item = add_item(cid, title="Ohtani", price=45000, min_cents=40000)
    ops.record_sale(add_item(cid, title="Sold card", price=1000), "ebay", 1000, None, db.today(), owner)
    db.create_item(consignor_id=cid, title="Submitted", status="submitted", submitted_by="consignor", asking_cents=500)
    ops.propose_price(item, "consignor", 50000, "please", None)
    for url in ["/", "/items", "/items?status=on_hand&q=oht", "/items/new", f"/items/{item['id']}", "/consignors",
                f"/consignors/{cid}", "/payouts", f"/payouts?consignor={cid}", "/money", "/square", "/settings",
                "/users", "/activity", "/account", f"/labels?ids={item['id']}", f"/labels?ids={item['id']}&format=avery5160"]:
        r = shop.get(url)
        assert r.status_code == 200, (url, r.text[:800])
    assert "Ohtani" in shop.get("/items?q=oht").text
    assert "asks for $500.00" in shop.get(f"/items/{item['id']}").text


def test_intake_photos_sale_payout(shop):
    r = shop.post("/consignors", data={"name": "Jen Park", "email": "jen@example.com", "payout_method": "zelle",
                                       "commission_pct": "20"})
    cid = int(re.search(r"/consignors/(\d+)", str(r.url)).group(1))
    r = shop.post("/items", data={"consignor_id": cid, "title": "Charizard ex", "price": "325", "list_now": "1",
                                  "quantity": "1"}, files=[("photos", ("front.png", PNG, "image/png")),
                                                           ("photos", ("evil.html", b"<script>", "text/html"))])
    assert r.status_code == 200 and "Not added: evil.html" in r.text
    item = db.list_items(consignor_id=cid)[0]
    assert item["status"] == "listed" and item["price_cents"] == 32500
    photo = db.list_photos(item["id"])[0]
    assert shop.get(f"/p/{photo['token']}.{photo['ext']}").content == PNG
    assert shop.get(f"/p/{photo['token']}.jpg").status_code == 404
    labels = shop.get(f"/labels?ids={item['id']}").text
    assert "<svg" in labels and item["sku"] in labels
    assert shop.get(f"/scan?code={item['sku'].lower()}", follow_redirects=False).headers["location"] == f"/items/{item['id']}"

    old = (db.today()[:4] + "-01-02")
    shop.post(f"/items/{item['id']}/sale", data={"channel": "ebay", "price": "325", "fees": "43.06", "sold_at": old})
    sale = db.list_sales(item_id=item["id"])[0]
    assert (sale["fees_cents"], sale["commission_cents"], sale["consignor_cents"]) == (4306, 5639, 22555)
    assert db.get_item(item["id"])["status"] == "sold"

    r = shop.post("/payouts", data={"consignor_id": cid, "amount": "999", "method": "zelle", "status": "sent"})
    assert "available to pay out" in r.text
    shop.post("/payouts", data={"consignor_id": cid, "amount": "225.55", "method": "zelle", "status": "sent"})
    assert ledger.consignor_balance(cid)["balance"] == 0

    csv = shop.get(f"/exports/ebay.csv?ids={item['id']}").text
    assert "/p/" + photo["token"] in csv and "*Action" in csv
    assert shop.get(f"/consignors/{cid}/statement.csv").text.count("\n") == 3


def test_staff_role_limits(make_client):
    staff = sign_in(make_client(), make_user("staff"))
    assert staff.get("/settings").status_code == 403
    assert staff.get("/users").status_code == 403
    assert staff.get("/items").status_code == 200


def test_consignor_portal_is_private(make_client, owner):
    mine = db.create_consignor("Mike")
    theirs = db.create_consignor("Jen")
    my_item = add_item(mine, title="My Ohtani", price=45000)
    their_item = add_item(theirs, title="Jen's Charizard", price=32500)
    c = sign_in(make_client(), make_user("consignor", "mike@example.com", consignor_id=mine))
    assert "My Ohtani" in c.get("/me").text and "Charizard" not in c.get("/me").text
    assert c.get(f"/me/items/{my_item['id']}").status_code == 200
    assert c.get(f"/me/items/{their_item['id']}").status_code == 404
    for url in ["/items", f"/items/{my_item['id']}", "/consignors", "/payouts", "/money", "/settings",
                "/exports/sales.csv", f"/consignors/{theirs}/statement.csv", f"/labels?ids={their_item['id']}"]:
        assert c.get(url).status_code == 404, url
    assert c.post(f"/items/{my_item['id']}/sale", data={"channel": "instore", "price": "1"}).status_code == 404
    assert c.post(f"/me/items/{their_item['id']}/propose", data={"price": "1"}).status_code == 404
    assert c.get("/", follow_redirects=False).headers["location"] == "/me"


def test_consignor_submits_and_negotiates(make_client, owner):
    cid = db.create_consignor("Mike")
    c = sign_in(make_client(), make_user("consignor", "mike@example.com", consignor_id=cid))
    r = c.post("/me/submit", data={"title": "1986 Fleer Jordan"})
    assert "accept the consignment agreement" in r.text
    c.post("/me/agree")
    r = c.post("/me/submit", data={"title": "1986 Fleer Jordan", "asking": "3000", "min": "2500",
                                   "commission_pct": "0"}, files=[("photos", ("f.png", PNG, "image/png"))])
    item = db.list_items(consignor_id=cid)[0]
    assert item["status"] == "submitted" and item["asking_cents"] == 300000 and item["commission_pct"] is None
    assert len(db.list_photos(item["id"])) == 1
    ops.receive(item, owner)
    ops.propose_price(db.get_item(item["id"]), "shop", 280000, "Market is about $2,800", owner)
    assert "the shop suggests" in c.get("/me").text
    p = db.pending_proposal(item["id"])
    c.post(f"/me/items/{item['id']}/proposals/{p['id']}", data={"answer": "accept"})
    assert db.get_item(item["id"])["price_cents"] == 280000
    c.post(f"/me/items/{item['id']}/propose", data={"price": "2900"})
    assert db.pending_proposal(item["id"])["proposed_by"] == "consignor"
    assert c.get("/me/statement").status_code == 200 and c.get("/me/statement.csv").status_code == 200
    c.post("/account/details", data={"payout_method": "paypal", "payout_details": "mike@paypal.test"})
    assert db.get_consignor(cid)["payout_method"] == "paypal"


def test_invite_link_sets_password(shop, make_client):
    cid = db.create_consignor("Mike", email="mike@example.com")
    page = shop.post(f"/consignors/{cid}/invite", data={"email": "mike@example.com"}).text
    link = re.search(r"/invite/([\w-]+)", page).group(1)
    c = make_client()
    r = c.post(f"/invite/{link}", data={"password": PASSWORD, "confirm": PASSWORD})
    assert "Hi, Mike" in r.text
    assert "expired" in make_client().get(f"/invite/{link}").text


def test_cross_site_post_refused(shop):
    assert shop.post("/consignors", data={"name": "x"}, headers={"origin": "https://evil.test"}).status_code == 403


def test_settings_save(shop):
    r = shop.post("/settings", data={"commission_tiers": "0:35, 250:25", "consignment_days": "60", "hold_days": "3",
                                     "fee_pct_ebay": "12", "fee_fixed_ebay": "0.30", "label_format": "avery5160"})
    assert "Settings saved" in r.text
    assert db.get_setting("commission_tiers") == [[0, 35.0], [250, 25.0]]
    assert db.get_setting("channel_fees")["ebay"] == [12.0, 30]
    assert "Could not read" in shop.post("/settings", data={"commission_tiers": "nope", "consignment_days": "1",
                                                            "hold_days": "1"}).text


def test_void_needs_owner(make_client, owner):
    cid = db.create_consignor("Mike")
    sid, _ = ops.record_sale(add_item(cid), "instore", 10000, 0, db.today(), owner)
    staff = sign_in(make_client(), make_user("staff"))
    assert staff.post(f"/sales/{sid}/void", data={"reason": "x"}).status_code == 403
    boss = sign_in(make_client(), owner)
    boss.post(f"/sales/{sid}/void", data={"reason": "Returned"})
    assert db.get_sale(sid)["voided_at"]


def test_transfer_and_accounts_page(shop):
    shop.post("/transfers", data={"direction": "to_consignment", "amount": "100"})
    assert "$100.00" in shop.get("/money").text


def test_labels_print_one_label_per_copy(shop):
    cid = db.create_consignor("Mike")
    item = add_item(cid, title="Ohtani", price=45000)
    page = shop.get(f"/labels?ids={item['id']}&copies=3").text
    assert page.count('<div class="label">') == 3 and page.count("<svg") == 3


def test_square_webhook_runs_a_sync(make_client, owner, monkeypatch):
    import base64
    import hashlib
    import hmac

    from consign import square
    calls = []
    monkeypatch.setattr(square, "sync_sales", lambda client, record: calls.append(client) or {"recorded": [], "skipped": []})
    monkeypatch.setenv("SQUARE_WEBHOOK_SIGNATURE_KEY", "key")
    monkeypatch.setenv("SQUARE_WEBHOOK_URL", "https://consign.example/webhooks/square")
    c = make_client(square_factory=lambda: "client")
    body = b'{"type":"order.updated"}'
    assert c.post("/webhooks/square", content=body, headers={"x-square-hmacsha256-signature": "bad"}).status_code == 401
    sig = base64.b64encode(hmac.new(b"key", b"https://consign.example/webhooks/square" + body, hashlib.sha256).digest()).decode()
    assert c.post("/webhooks/square", content=body, headers={"x-square-hmacsha256-signature": sig}).status_code == 200
    assert calls == ["client"]
