"""Square: push consigned items into the Square item library, and pull in-store sales.

* **Push**: each listed item becomes a Square item with one variation whose SKU
  is the item's SKU (the barcode on its label) and whose stock count is its
  quantity at your location. Scanning the label at the register finds it.
* **Stock**: when an item sells elsewhere (eBay, Whatnot...) or is returned to
  the consignor, its Square stock is set to 0 so it cannot be sold twice.
* **Sales**: completed Square orders are read back; any line for a consigned
  item is recorded as an in-store sale and split like any other sale. A Square
  webhook can trigger this as soon as an order completes.

Settings (environment variables):
  SQUARE_ACCESS_TOKEN     from the Square Developer Dashboard (production or sandbox)
  SQUARE_LOCATION_ID      the store's location id
  SQUARE_ENVIRONMENT      production (default) | sandbox
  SQUARE_WEBHOOK_SIGNATURE_KEY and SQUARE_WEBHOOK_URL  to accept webhooks
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import datetime, timedelta, timezone

import httpx

from . import db, money

API_VERSION = "2025-01-23"


def configured() -> bool:
    return bool(os.environ.get("SQUARE_ACCESS_TOKEN") and os.environ.get("SQUARE_LOCATION_ID"))


def base_url() -> str:
    if os.environ.get("SQUARE_ENVIRONMENT", "production").lower() == "sandbox":
        return "https://connect.squareupsandbox.com"
    return "https://connect.squareup.com"


class SquareError(Exception):
    pass


class Square:
    def __init__(self, token: str | None = None, location_id: str | None = None, transport=None):
        self.location_id = location_id or os.environ.get("SQUARE_LOCATION_ID", "")
        self.http = httpx.Client(
            base_url=base_url(), transport=transport, timeout=30,
            headers={"Authorization": f"Bearer {token or os.environ.get('SQUARE_ACCESS_TOKEN', '')}",
                     "Square-Version": API_VERSION, "Content-Type": "application/json"})

    def _call(self, method: str, path: str, json: dict | None = None) -> dict:
        r = self.http.request(method, path, json=json)
        data = r.json() if r.content else {}
        if r.status_code >= 400:
            errors = "; ".join(e.get("detail") or e.get("code", "") for e in data.get("errors", [])) or r.text[:300]
            raise SquareError(f"Square said: {errors}")
        return data

    # --- catalog -----------------------------------------------------------

    def upsert_item(self, item: dict) -> tuple[str, str]:
        """Create or update the Square item for ``item``. Returns (item id, variation id)."""
        name = item["title"][:255]
        description = _description(item)
        variation = {
            "type": "ITEM_VARIATION",
            "id": item.get("square_variation_id") or f"#var{item['id']}",
            "present_at_all_locations": True,
            "item_variation_data": {
                "name": "Regular",
                "sku": item["sku"],
                "pricing_type": "FIXED_PRICING",
                "price_money": {"amount": item["price_cents"] or 0, "currency": "USD"},
                "track_inventory": True,
            },
        }
        obj = {
            "type": "ITEM",
            "id": item.get("square_item_id") or f"#item{item['id']}",
            "present_at_all_locations": True,
            "item_data": {"name": name, "description": description, "variations": [variation]},
        }
        if item.get("square_item_id"):
            # Updates must carry the current version of the item and the variation.
            current = self._call("GET", f"/v2/catalog/object/{item['square_item_id']}").get("object", {})
            obj["version"] = current.get("version")
            for v in current.get("item_data", {}).get("variations", []):
                if v["id"] == item.get("square_variation_id"):
                    variation["version"] = v.get("version")
            variation["item_variation_data"]["item_id"] = item["square_item_id"]
        data = self._call("POST", "/v2/catalog/object", {"idempotency_key": str(uuid.uuid4()), "object": obj})
        saved = data["catalog_object"]
        return saved["id"], saved["item_data"]["variations"][0]["id"]

    def set_stock(self, variation_id: str, quantity: int) -> None:
        self._call("POST", "/v2/inventory/changes/batch-create", {
            "idempotency_key": str(uuid.uuid4()),
            "changes": [{"type": "PHYSICAL_COUNT", "physical_count": {
                "catalog_object_id": variation_id, "state": "IN_STOCK", "location_id": self.location_id,
                "quantity": str(max(quantity, 0)),
                "occurred_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")}}],
        })

    # --- orders ------------------------------------------------------------

    def completed_orders(self, since_iso: str) -> list[dict]:
        orders, cursor = [], None
        while True:
            body = {"location_ids": [self.location_id], "limit": 200, "query": {
                "filter": {"state_filter": {"states": ["COMPLETED"]},
                           "date_time_filter": {"closed_at": {"start_at": since_iso}}},
                "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"}}}
            if cursor:
                body["cursor"] = cursor
            data = self._call("POST", "/v2/orders/search", body)
            orders += data.get("orders", [])
            cursor = data.get("cursor")
            if not cursor:
                return orders


def _description(item: dict) -> str:
    parts = [item.get("consignor_code") and f"Consigned {item['consignor_code']}",
             item.get("grader") and f"{item['grader']} {item.get('grade') or ''}".strip(),
             item.get("cert_number") and f"Cert {item['cert_number']}"]
    return " · ".join(p for p in parts if p)[:4000]


# --- what the app calls ----------------------------------------------------

def push_item(client: Square, item: dict) -> dict:
    """Send one item to Square and set its stock. Items not for sale get stock 0."""
    item_id, var_id = client.upsert_item(item)
    for_sale = item["status"] == "listed"
    client.set_stock(var_id, item["quantity"] if for_sale else 0)
    db.update_item(item["id"], square_item_id=item_id, square_variation_id=var_id, square_synced_at=db.now())
    return db.get_item(item["id"])


def zero_stock(client: Square, item: dict) -> None:
    if item.get("square_variation_id"):
        client.set_stock(item["square_variation_id"], 0)


def line_price(line: dict) -> int:
    """What the buyer paid for a line, before tax: gross sales less discounts."""
    gross = (line.get("gross_sales_money") or line.get("base_price_money") or {}).get("amount", 0)
    discount = (line.get("total_discount_money") or {}).get("amount", 0)
    return int(gross) - int(discount)


def sync_sales(client: Square, record_sale) -> dict:
    """Record in-store sales of consigned items since the last sync.

    ``record_sale(item, price_cents, sold_at, order_id)`` is the same function
    the Record sale form uses, so the split and stock updates are identical.
    """
    last = db.get_setting("square_last_sync")
    since = last or (datetime.now(timezone.utc) - timedelta(days=30)).isoformat(timespec="seconds")
    started = db.now()
    recorded, skipped = [], []
    for order in client.completed_orders(since):
        for line in order.get("line_items", []):
            vid = line.get("catalog_object_id")
            item = db.get_item_by_square_variation(vid) if vid else None
            if not item:
                continue
            if db.square_order_seen(order["id"], item["id"]):
                continue
            if item["status"] != "listed":
                skipped.append(f"{item['sku']} was already {item['status']} when Square order {order['id']} sold it")
                continue
            sold_at = (order.get("closed_at") or order.get("created_at") or db.today())[:10]
            record_sale(item, line_price(line), sold_at, order["id"])
            recorded.append(item["sku"])
    db.set_setting("square_last_sync", started)
    return {"recorded": recorded, "skipped": skipped, "since": since}


def verify_webhook(body: bytes, signature: str, url: str | None = None, key: str | None = None) -> bool:
    """Square signs webhooks with HMAC-SHA256 over the notification URL plus the raw body."""
    key = key or os.environ.get("SQUARE_WEBHOOK_SIGNATURE_KEY", "")
    url = url or os.environ.get("SQUARE_WEBHOOK_URL", "")
    if not key or not url or not signature:
        return False
    digest = hmac.new(key.encode(), url.encode() + body, hashlib.sha256).digest()
    return hmac.compare_digest(base64.b64encode(digest).decode(), signature)


def csv_rows(items: list[dict]) -> tuple[list[str], list[list]]:
    """A file for Square's item import, for shops not using the API connection.

    Column names follow Square's item import spreadsheet. Download a fresh
    template from Square (Items > Actions > Import) if the import complains.
    """
    header = ["Item Name", "Variation Name", "SKU", "Description", "Categories", "Price",
              "Track Inventory", "New Quantity"]
    rows = [[i["title"][:128], "Regular", i["sku"], _description(i), i.get("category") or "Consignment",
             money.plain(i["price_cents"]), "Y", i["quantity"] if i["status"] == "listed" else 0] for i in items]
    return header, rows
