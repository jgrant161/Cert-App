import base64
import hashlib
import hmac
import json

import httpx
import pytest
from conftest import add_item

from consign import db, ops, square


class FakeSquare:
    """Just enough of the Square API to check what the app sends and how it reads replies."""

    def __init__(self, orders=()):
        self.calls = []
        self.orders = list(orders)
        self.stock = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        self.calls.append((request.method, request.url.path, body))
        assert request.headers["Authorization"] == "Bearer tok"
        if request.url.path == "/v2/catalog/object" and request.method == "POST":
            obj = body["object"]
            var = obj["item_data"]["variations"][0]
            item_id = obj["id"] if not obj["id"].startswith("#") else "SQ_ITEM_" + obj["id"][5:]
            var_id = var["id"] if not var["id"].startswith("#") else "SQ_VAR_" + var["id"][4:]
            return httpx.Response(200, json={"catalog_object": {"id": item_id, "item_data": {
                "variations": [{"id": var_id}]}}})
        if request.url.path.startswith("/v2/catalog/object/"):
            oid = request.url.path.rsplit("/", 1)[1]
            return httpx.Response(200, json={"object": {"id": oid, "version": 7, "item_data": {
                "variations": [{"id": oid.replace("ITEM", "VAR"), "version": 8}]}}})
        if request.url.path == "/v2/inventory/changes/batch-create":
            c = body["changes"][0]["physical_count"]
            self.stock[c["catalog_object_id"]] = c["quantity"]
            return httpx.Response(200, json={})
        if request.url.path == "/v2/orders/search":
            return httpx.Response(200, json={"orders": self.orders})
        return httpx.Response(404, json={"errors": [{"detail": "not found"}]})

    def client(self):
        return square.Square("tok", "LOC", transport=httpx.MockTransport(self.handler))


@pytest.fixture
def cid():
    return db.create_consignor("Mike")


def test_push_creates_then_updates_with_versions(cid):
    fake = FakeSquare()
    item = add_item(cid, title="Ohtani PSA 10", price=45000)
    item = square.push_item(fake.client(), item)
    assert item["square_item_id"] == f"SQ_ITEM_{item['id']}" and item["square_variation_id"] == f"SQ_VAR_{item['id']}"
    sent = fake.calls[0][2]["object"]["item_data"]["variations"][0]["item_variation_data"]
    assert sent["sku"] == item["sku"] and sent["price_money"] == {"amount": 45000, "currency": "USD"}
    assert fake.stock[item["square_variation_id"]] == "1"
    db.update_item(item["id"], price_cents=40000)
    square.push_item(fake.client(), db.get_item(item["id"]))
    update = fake.calls[-2][2]["object"]
    assert update["version"] == 7 and update["item_data"]["variations"][0]["version"] == 8


def test_sold_elsewhere_zeroes_square_stock(cid, owner):
    fake = FakeSquare()
    item = square.push_item(fake.client(), add_item(cid, price=1000))
    ops.record_sale(item, "ebay", 1000, None, db.today(), owner, sq=fake.client())
    assert fake.stock[item["square_variation_id"]] == "0"


def test_sync_records_register_sales_once(cid, owner):
    item = add_item(cid, price=20000)
    db.update_item(item["id"], square_variation_id="VAR1", square_item_id="IT1")
    other = add_item(cid, price=5000)
    order = {"id": "ORD1", "closed_at": "2026-10-01T18:00:00Z", "line_items": [
        {"catalog_object_id": "VAR1", "gross_sales_money": {"amount": 20000}, "total_discount_money": {"amount": 1000}},
        {"catalog_object_id": "UNRELATED", "gross_sales_money": {"amount": 500}}]}
    fake = FakeSquare([order])

    def record(i, price, sold_at, oid):
        ops.record_sale(i, "instore", price, None, sold_at, owner, oid, square_order_id=oid, allow_below_floor=True)
    result = square.sync_sales(fake.client(), record)
    assert result["recorded"] == [item["sku"]]
    sale = db.list_sales(item_id=item["id"])[0]
    assert (sale["price_cents"], sale["sold_at"], sale["square_order_id"]) == (19000, "2026-10-01", "ORD1")
    assert not any(c[1] == "/v2/inventory/changes/batch-create" for c in fake.calls)  # Square already counted it
    assert square.sync_sales(fake.client(), record)["recorded"] == []
    assert db.get_item(other["id"])["status"] == "listed"


def test_square_errors_are_readable():
    fake = FakeSquare()
    with pytest.raises(square.SquareError, match="not found"):
        fake.client()._call("GET", "/v2/nope")


def test_webhook_signature():
    body = b'{"type":"order.updated"}'
    url = "https://consign.dugouttx.com/webhooks/square"
    sig = base64.b64encode(hmac.new(b"key", url.encode() + body, hashlib.sha256).digest()).decode()
    assert square.verify_webhook(body, sig, url, "key")
    assert not square.verify_webhook(body + b" ", sig, url, "key")
    assert not square.verify_webhook(body, sig, url, "")


def test_square_import_file(cid):
    header, rows = square.csv_rows([db.get_item(add_item(cid, title="Box", price=4999, quantity=3)["id"])])
    assert header[:3] == ["Item Name", "Variation Name", "SKU"] and rows[0][5] == "49.99" and rows[0][7] == 3
