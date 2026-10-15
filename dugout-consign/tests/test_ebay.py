import json
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from conftest import PNG, add_item, make_user, sign_in

from consign import db, ebay, ops

DESCRIPTORS = {"itemConditionPolicies": [{"categoryId": "261328", "itemConditions": [
    {"conditionId": "2750", "conditionDescriptors": [
        {"conditionDescriptorId": "27501", "conditionDescriptorValues": [
            {"conditionDescriptorValueId": "275010", "conditionDescriptorValueName": "Professional Sports Authenticator (PSA)"},
            {"conditionDescriptorValueId": "275013", "conditionDescriptorValueName": "Beckett Grading Services (BGS)"}]},
        {"conditionDescriptorId": "27502", "conditionDescriptorValues": [
            {"conditionDescriptorValueId": "275020", "conditionDescriptorValueName": "10"},
            {"conditionDescriptorValueId": "275021", "conditionDescriptorValueName": "9.5"},
            {"conditionDescriptorValueId": "275022", "conditionDescriptorValueName": "9"}]}]},
    {"conditionId": "4000", "conditionDescriptors": [
        {"conditionDescriptorId": "40001", "conditionDescriptorValues": [
            {"conditionDescriptorValueId": "400010", "conditionDescriptorValueName": "Near mint or better"},
            {"conditionDescriptorValueId": "400011", "conditionDescriptorValueName": "Excellent"},
            {"conditionDescriptorValueId": "400012", "conditionDescriptorValueName": "Very good"},
            {"conditionDescriptorValueId": "400013", "conditionDescriptorValueName": "Poor"}]}]}]}]}


class FakeEbay:
    def __init__(self, orders=()):
        self.calls = []
        self.orders = list(orders)
        self.offers = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        path, method = request.url.path, request.method
        body = json.loads(request.content) if request.content and "json" in request.headers.get("content-type", "") else None
        self.calls.append((method, path, body))
        if path == "/identity/v1/oauth2/token":
            form = parse_qs(request.content.decode())
            assert request.headers["Authorization"].startswith("Basic ")
            if form["grant_type"] == ["authorization_code"]:
                return httpx.Response(200, json={"refresh_token": "RT", "refresh_token_expires_in": 47304000,
                                                 "access_token": "AT"})
            assert form["refresh_token"] == ["RT"]
            return httpx.Response(200, json={"access_token": "AT", "expires_in": 7200})
        assert request.headers["Authorization"] == "Bearer AT"
        if path.endswith("get_item_condition_policies"):
            return httpx.Response(200, json=DESCRIPTORS)
        if path.startswith("/sell/inventory/v1/inventory_item/"):
            return httpx.Response(204)
        if path == "/sell/inventory/v1/offer" and method == "GET":
            sku = request.url.params["sku"]
            if sku in self.offers:
                return httpx.Response(200, json={"offers": [{"offerId": self.offers[sku]}]})
            return httpx.Response(404, json={"errors": [{"message": "No offer found"}]})
        if path == "/sell/inventory/v1/offer" and method == "POST":
            self.offers[body["sku"]] = f"OF-{body['sku']}"
            return httpx.Response(201, json={"offerId": self.offers[body["sku"]]})
        if path.startswith("/sell/inventory/v1/offer/") and path.endswith("/publish"):
            return httpx.Response(200, json={"listingId": "LIST-" + path.split("/")[-2]})
        if path.startswith("/sell/inventory/v1/offer/") and path.endswith("/withdraw"):
            return httpx.Response(200, json={"listingId": "x"})
        if path.startswith("/sell/inventory/v1/offer/") and method == "PUT":
            return httpx.Response(204)
        if path.startswith("/sell/account/v1/"):
            kind = path.split("/")[-1].replace("_policy", "")
            return httpx.Response(200, json={f"{kind}Policies": [{f"{kind}PolicyId": f"{kind}-1", "name": f"Std {kind}"}]})
        if path.startswith("/sell/inventory/v1/location/"):
            return httpx.Response(404 if method == "GET" else 204, json={} if method == "GET" else None)
        if path == "/sell/fulfillment/v1/order":
            return httpx.Response(200, json={"orders": self.orders, "total": len(self.orders)})
        return httpx.Response(500, json={"errors": [{"message": f"unexpected {method} {path}"}]})

    def client(self):
        return ebay.Ebay(transport=httpx.MockTransport(self.handler))

    def sent(self, method, prefix):
        return [b for m, p, b in self.calls if m == method and p.startswith(prefix)]


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "app")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_RUNAME", "Dug_Out-DugOut-Consig-abcdef")
    monkeypatch.setenv("CONSIGN_BASE_URL", "https://consign.example")
    db.set_setting("ebay_refresh_token", "RT")
    for kind in ("fulfillment", "payment", "return"):
        db.set_setting(f"ebay_{kind}_policy", f"{kind}-1")
    return FakeEbay()


def card(cid, **kw):
    item = add_item(cid, **{"title": "Ohtani", "price": 45000, "sport": "Baseball", "subject": "Shohei Ohtani",
                            "year": "2018", **kw})
    db.add_photo(item["id"], f"tok{item['id']}", "jpg", None)
    return db.get_item(item["id"])


def test_consent_and_code_exchange(monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "app")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_RUNAME", "RU")
    q = parse_qs(urlsplit(ebay.consent_url("st")).query)
    assert q["redirect_uri"] == ["RU"] and q["state"] == ["st"] and "sell.inventory" in q["scope"][0]
    ebay.exchange_code("CODE", transport=httpx.MockTransport(FakeEbay().handler))
    assert db.get_setting("ebay_refresh_token") == "RT" and ebay.connected()


def test_graded_card_listing_then_revision(ready):
    cid = db.create_consignor("Mike")
    item = card(cid, grader="PSA", grade="10", cert_number="81234567")
    item = ebay.push(ready.client(), item)
    inv = ready.sent("PUT", "/sell/inventory/v1/inventory_item/")[0]
    assert inv["condition"] == "LIKE_NEW"
    assert inv["conditionDescriptors"] == [{"name": "27501", "values": ["275010"]}, {"name": "27502", "values": ["275020"]},
                                           {"name": "27503", "additionalInfo": "81234567"}]
    assert inv["product"]["imageUrls"] == [f"https://consign.example/p/tok{item['id']}.jpg"]
    assert inv["product"]["aspects"]["Player/Athlete"] == ["Shohei Ohtani"]
    offer = ready.sent("POST", "/sell/inventory/v1/offer")[0]
    assert offer["categoryId"] == "261328" and offer["pricingSummary"]["price"]["value"] == "450.00"
    assert offer["listingPolicies"]["returnPolicyId"] == "return-1" and offer["merchantLocationKey"] == "DUGOUT"
    assert (item["ebay_offer_id"], item["ebay_listing_id"]) == (f"OF-{item['sku']}", f"LIST-OF-{item['sku']}")
    assert ebay.listing_url(item).endswith(item["ebay_listing_id"])
    # a price change revises the live listing; no second publish
    ops.set_price(item, 40000, None, eb=ready.client())
    assert ready.sent("PUT", "/sell/inventory/v1/offer/")[-1]["pricingSummary"]["price"]["value"] == "400.00"
    assert len([1 for m, p, _ in ready.calls if p.endswith("/publish")]) == 1


def test_raw_and_tcg_cards(ready):
    cid = db.create_consignor("Jen")
    item = ebay.push(ready.client(), card(cid, condition="Excellent (EX)", sport="Pokemon", subject="Charizard"))
    inv = ready.sent("PUT", "/sell/inventory/v1/inventory_item/")[0]
    assert inv["condition"] == "USED_VERY_GOOD" and inv["conditionDescriptors"] == [{"name": "40001", "values": ["400011"]}]
    assert inv["product"]["aspects"]["Character"] == ["Charizard"]
    assert ready.sent("POST", "/sell/inventory/v1/offer")[0]["categoryId"] == "183454"
    assert item["ebay_listing_id"]


def test_listing_problems_are_explained(ready):
    cid = db.create_consignor("Mike")
    with pytest.raises(ebay.EbayError, match="grader"):
        ebay.push(ready.client(), card(cid, grader="XYZ", grade="10"))
    with pytest.raises(ebay.EbayError, match="grade"):
        ebay.push(ready.client(), card(cid, grader="BGS", grade="11"))
    no_photo = add_item(cid, title="No photo", price=100)
    with pytest.raises(ebay.EbayError, match="photo"):
        ebay.push(ready.client(), no_photo)
    db.set_setting("ebay_return_policy", None)
    with pytest.raises(ebay.EbayError, match="policies"):
        ebay.push(ready.client(), card(cid))


def test_sold_in_store_ends_the_ebay_listing(ready, owner):
    cid = db.create_consignor("Mike")
    item = ebay.push(ready.client(), card(cid))
    ops.record_sale(item, "instore", 45000, None, db.today(), owner, eb=ready.client())
    assert ready.sent("POST", f"/sell/inventory/v1/offer/{item['ebay_offer_id']}/withdraw")
    assert db.get_item(item["id"])["ebay_listing_id"] is None


def test_returned_item_ends_listing(ready, owner):
    cid = db.create_consignor("Mike")
    item = ebay.push(ready.client(), card(cid))
    ops.close_item(item, "returned", owner, eb=ready.client())
    assert ready.sent("POST", f"/sell/inventory/v1/offer/{item['ebay_offer_id']}/withdraw")


def test_order_sync(ready, owner):
    cid = db.create_consignor("Mike", commission_pct=20.0)
    one = card(cid)
    two = add_item(cid, title="Box", price=5000, quantity=3)
    paid = {"orderId": "11-1", "creationDate": "2026-10-10T15:00:00.000Z", "orderPaymentStatus": "PAID",
            "totalMarketplaceFee": {"value": "66.50", "currency": "USD"},
            "lineItems": [{"sku": one["sku"], "quantity": 1, "lineItemCost": {"value": "450.00"}},
                          {"sku": two["sku"], "quantity": 2, "lineItemCost": {"value": "100.00"}},
                          {"sku": "NOT-OURS", "quantity": 1, "lineItemCost": {"value": "5.00"}}]}
    unpaid = {"orderId": "11-2", "creationDate": "2026-10-11T15:00:00.000Z", "orderPaymentStatus": "PENDING",
              "lineItems": [{"sku": two["sku"], "quantity": 1, "lineItemCost": {"value": "50.00"}}]}
    cancelled = dict(unpaid, orderId="11-3", orderPaymentStatus="PAID", cancelStatus={"cancelState": "CANCELED"})
    ready.orders = [paid, unpaid, cancelled]

    def record(item, price, fees, sold_at, oid):
        ops.record_sale(item, "ebay", price, fees, sold_at, owner, oid, allow_below_floor=True)
    result = ebay.sync_orders(ready.client(), record)
    assert sorted(result["recorded"]) == sorted([one["sku"], two["sku"], two["sku"]])
    s1 = db.list_sales(item_id=one["id"])[0]
    assert (s1["price_cents"], s1["fees_cents"], s1["sold_at"], s1["external_ref"]) == (45000, 5392, "2026-10-10", "11-1")   # fee shared by line value
    box = db.list_sales(item_id=two["id"])
    assert [s["price_cents"] for s in box] == [5000, 5000] and sum(s["fees_cents"] for s in box) == 1198
    assert db.get_item(two["id"])["quantity"] == 1 and db.get_item(one["id"])["status"] == "sold"
    assert ebay.sync_orders(ready.client(), record)["recorded"] == []    # nothing twice


def test_web_connect_flow_and_page(make_client, owner, monkeypatch):
    monkeypatch.setenv("EBAY_CLIENT_ID", "app")
    monkeypatch.setenv("EBAY_CLIENT_SECRET", "secret")
    monkeypatch.setenv("EBAY_RUNAME", "RU")
    fake = FakeEbay()
    monkeypatch.setattr(ebay, "exchange_code", lambda code: db.set_setting("ebay_refresh_token", "RT"))
    c = sign_in(make_client(ebay_factory=fake.client), owner)
    assert "Connect eBay" in c.get("/ebay").text
    r = c.get("/ebay/connect", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    assert "did not finish" in c.get("/ebay/callback?code=X&state=wrong").text
    r = c.get("/ebay/connect", follow_redirects=False)
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    page = c.get(f"/ebay/callback?code=X&state={state}").text
    assert "eBay is connected" in page and "Std fulfillment" in page
    r = c.post("/ebay/setup", data={"fulfillment": "fulfillment-1", "payment": "payment-1", "return": "return-1",
                                    "addressLine1": "211 Midway St Ste R", "city": "Spring", "stateOrProvince": "TX",
                                    "postalCode": "77373"})
    assert "You can list items on eBay now" in r.text
    assert fake.sent("POST", "/sell/inventory/v1/location/DUGOUT")[0]["location"]["address"]["country"] == "US"
    staff = sign_in(make_client(ebay_factory=fake.client), make_user("staff"))
    assert staff.get("/ebay/connect").status_code == 403


def test_item_button(make_client, owner, ready):
    cid = db.create_consignor("Mike")
    item = card(cid, grader="PSA", grade="9")
    c = sign_in(make_client(ebay_factory=ready.client), owner)
    assert "List on eBay" in c.get(f"/items/{item['id']}").text
    r = c.post(f"/items/{item['id']}/ebay", data={"action": "push"})
    assert "Listed on eBay" in r.text and "on eBay</a>" in r.text
    r = c.post(f"/items/{item['id']}/ebay", data={"action": "end"})
    assert "listing ended" in r.text


def test_backup_and_privacy(make_client, owner):
    cid = db.create_consignor("Mike")
    item = add_item(cid)
    c = sign_in(make_client(), owner)
    c.post(f"/items/{item['id']}/photos", files=[("photos", ("a.png", PNG, "image/png"))])
    r = c.get("/settings/backup")
    if db.is_postgres():
        assert "use your host" in r.text      # PostgreSQL is backed up by the host, not by this button
    else:
        import io
        import zipfile
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert "consign.db" in names and any(n.startswith("files/photos/") for n in names)
    assert make_client().get("/privacy").status_code == 200
