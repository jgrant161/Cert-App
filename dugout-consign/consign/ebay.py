"""eBay: list consigned items straight to the shop's eBay account, and read eBay orders back.

* **Connect**: the owner clicks *Connect eBay* once and approves on eBay's site.
  eBay sends back a long-lived refresh token, kept in the database; the app
  trades it for a short-lived access token whenever it calls eBay.
* **List**: each item becomes an eBay inventory item (keyed by its SKU) and an
  offer in Sports Trading Card Singles or CCG Individual Cards, published as a
  fixed-price listing with the shop's shipping, payment and return policies.
  Graded cards carry eBay's required grader / grade / cert condition
  descriptors; raw cards carry a card condition.
* **Stock**: when an item sells anywhere else or goes back to the consignor,
  its eBay listing is ended. A price change updates the live listing.
* **Orders**: paid, uncancelled eBay orders for consigned SKUs are recorded as
  eBay sales, with eBay's own fee when the order reports one.

Settings (environment variables):
  EBAY_CLIENT_ID      App ID (Client ID) of the production keyset
  EBAY_CLIENT_SECRET  Cert ID (Client Secret)
  EBAY_RUNAME         the RuName (eBay Redirect URL name) whose accepted URL is <portal>/ebay/callback
  EBAY_ENVIRONMENT    production (default) | sandbox
"""

from __future__ import annotations

import base64
import os
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlencode

import httpx

from . import db, listings, money

MARKETPLACE = "EBAY_US"
LOCATION_KEY = "DUGOUT"
SCOPES = [
    "https://api.ebay.com/oauth/api_scope",
    "https://api.ebay.com/oauth/api_scope/sell.inventory",
    "https://api.ebay.com/oauth/api_scope/sell.account",
    "https://api.ebay.com/oauth/api_scope/sell.fulfillment",
]
GRADED, UNGRADED = "2750", "4000"                 # trading card condition ids
GRADED_ENUM, UNGRADED_ENUM = "LIKE_NEW", "USED_VERY_GOOD"
GRADER_ID, GRADE_ID, CERT_ID, CARD_CONDITION_ID = "27501", "27502", "27503", "40001"


def configured() -> bool:
    return all(os.environ.get(k) for k in ("EBAY_CLIENT_ID", "EBAY_CLIENT_SECRET", "EBAY_RUNAME"))


def sandbox() -> bool:
    return os.environ.get("EBAY_ENVIRONMENT", "production").lower() == "sandbox"


def api_host() -> str:
    return "https://api.sandbox.ebay.com" if sandbox() else "https://api.ebay.com"


def auth_host() -> str:
    return "https://auth.sandbox.ebay.com" if sandbox() else "https://auth.ebay.com"


def connected() -> bool:
    return configured() and bool(db.get_setting("ebay_refresh_token"))


def consent_url(state: str) -> str:
    return f"{auth_host()}/oauth2/authorize?" + urlencode({
        "client_id": os.environ["EBAY_CLIENT_ID"], "redirect_uri": os.environ["EBAY_RUNAME"],
        "response_type": "code", "scope": " ".join(SCOPES), "state": state})


class EbayError(Exception):
    pass


def _basic() -> str:
    raw = f"{os.environ.get('EBAY_CLIENT_ID', '')}:{os.environ.get('EBAY_CLIENT_SECRET', '')}"
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _errors(r: httpx.Response) -> str:
    try:
        data = r.json()
    except ValueError:
        return r.text[:300]
    if "errors" in data:
        return "; ".join(e.get("longMessage") or e.get("message", "") for e in data["errors"])
    return data.get("error_description") or str(data)[:300]


def exchange_code(code: str, transport=None) -> None:
    """Finish Connect eBay: swap the one-time code for a refresh token and keep it."""
    with httpx.Client(transport=transport, timeout=30) as http:
        r = http.post(f"{api_host()}/identity/v1/oauth2/token",
                      headers={"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"},
                      data={"grant_type": "authorization_code", "code": code,
                            "redirect_uri": os.environ["EBAY_RUNAME"]})
    if r.status_code >= 400:
        raise EbayError(f"eBay did not accept the sign-in: {_errors(r)}")
    data = r.json()
    db.set_setting("ebay_refresh_token", data["refresh_token"])
    expires = datetime.now(timezone.utc) + timedelta(seconds=int(data.get("refresh_token_expires_in", 0)))
    db.set_setting("ebay_refresh_expires", expires.date().isoformat())
    db.set_setting("ebay_connected_at", db.now())


def disconnect() -> None:
    for key in ("ebay_refresh_token", "ebay_refresh_expires", "ebay_connected_at"):
        db.set_setting(key, None)


class Ebay:
    def __init__(self, transport=None):
        self.http = httpx.Client(base_url=api_host(), transport=transport, timeout=40)
        self._token: str | None = None

    def token(self) -> str:
        if self._token:
            return self._token
        refresh = db.get_setting("ebay_refresh_token")
        if not refresh:
            raise EbayError("eBay is not connected. Open the eBay page and click Connect eBay.")
        r = self.http.post("/identity/v1/oauth2/token",
                           headers={"Authorization": _basic(), "Content-Type": "application/x-www-form-urlencoded"},
                           data={"grant_type": "refresh_token", "refresh_token": refresh, "scope": " ".join(SCOPES)})
        if r.status_code >= 400:
            raise EbayError(f"eBay sign-in has expired or was revoked; click Connect eBay again. ({_errors(r)})")
        self._token = r.json()["access_token"]
        return self._token

    def call(self, method: str, path: str, json=None, params=None, ok404: bool = False):
        r = self.http.request(method, path, json=json, params=params, headers={
            "Authorization": f"Bearer {self.token()}", "Content-Type": "application/json",
            "Content-Language": "en-US", "Accept-Language": "en-US"})
        if ok404 and r.status_code == 404:
            return None
        if r.status_code >= 400:
            raise EbayError(f"eBay said: {_errors(r)}")
        return r.json() if r.content else {}

    # --- account set-up ----------------------------------------------------

    def policies(self) -> dict[str, list[dict]]:
        out = {}
        for kind in ("fulfillment", "payment", "return"):
            data = self.call("GET", f"/sell/account/v1/{kind}_policy", params={"marketplace_id": MARKETPLACE})
            out[kind] = [{"id": p[f"{kind}PolicyId"], "name": p["name"]} for p in data.get(f"{kind}Policies", [])]
        return out

    def ensure_location(self, address: dict) -> None:
        if self.call("GET", f"/sell/inventory/v1/location/{LOCATION_KEY}", ok404=True) is not None:
            return
        self.call("POST", f"/sell/inventory/v1/location/{LOCATION_KEY}", json={
            "name": money.setting("shop_name"), "merchantLocationStatus": "ENABLED", "locationTypes": ["STORE"],
            "location": {"address": {**address, "country": "US"}}})

    def condition_values(self, category_id: str) -> dict[str, dict[str, str]]:
        """eBay's ids for graders, grades and raw-card conditions, looked up once and kept."""
        key = f"ebay_descriptors_{category_id}"
        cached = db.get_setting(key)
        if cached:
            return cached
        data = self.call("GET", f"/sell/metadata/v1/marketplace/{MARKETPLACE}/get_item_condition_policies",
                         params={"filter": f"categoryIds:{{{category_id}}}"})
        out: dict[str, dict[str, str]] = {}
        for policy in data.get("itemConditionPolicies", []):
            for cond in policy.get("itemConditions", []):
                for d in cond.get("conditionDescriptors", []):
                    values = {v["conditionDescriptorValueName"]: v["conditionDescriptorValueId"]
                              for v in d.get("conditionDescriptorValues", [])}
                    out.setdefault(str(d["conditionDescriptorId"]), {}).update(values)
        if out:
            db.set_setting(key, out)
        return out

    # --- listings ----------------------------------------------------------

    def find_offer(self, sku: str) -> dict | None:
        data = self.call("GET", "/sell/inventory/v1/offer", params={"sku": sku, "marketplace_id": MARKETPLACE},
                         ok404=True)
        offers = (data or {}).get("offers", [])
        return offers[0] if offers else None

    def withdraw(self, offer_id: str) -> None:
        self.call("POST", f"/sell/inventory/v1/offer/{offer_id}/withdraw")

    def orders_since(self, since_iso: str) -> list[dict]:
        orders, offset = [], 0
        while True:
            data = self.call("GET", "/sell/fulfillment/v1/order", params={
                "filter": f"creationdate:[{since_iso}..]", "limit": 200, "offset": offset})
            batch = data.get("orders", [])
            orders += batch
            offset += len(batch)
            if not batch or offset >= int(data.get("total", 0)):
                return orders


# --- building the listing ---------------------------------------------------

def category(item: dict) -> str:
    return listings.EBAY_CCG_CARDS if listings.is_tcg(item) else listings.EBAY_SPORTS_CARDS


def _pick(options: dict[str, str], wanted: str) -> str | None:
    """Match our text ('PSA', '9.5', 'near mint') to one of eBay's labels."""
    if not wanted:
        return None
    w = wanted.strip().lower()
    for label, vid in options.items():
        if label.strip().lower() == w:
            return vid
    for label, vid in options.items():
        if re.search(rf"(^|[\s(]){re.escape(w)}($|[\s)])", label.lower()):
            return vid
    try:
        number = float(w)
        for label, vid in options.items():
            m = re.match(r"\s*([\d.]+)", label)
            if m and float(m.group(1)) == number:
                return vid
    except ValueError:
        pass
    return None


RAW_CONDITIONS = [("near mint", "near mint"), ("nm", "near mint"), ("mint", "near mint"), ("excellent", "excellent"),
                  ("ex", "excellent"), ("very good", "very good"), ("vg", "very good"), ("poor", "poor")]


def condition(item: dict, ids: dict[str, dict[str, str]]) -> tuple[str, list[dict]]:
    """The condition enum and descriptors eBay requires for trading cards."""
    if item.get("grader"):
        grader = _pick(ids.get(GRADER_ID, {}), item["grader"])
        grade = _pick(ids.get(GRADE_ID, {}), item.get("grade") or "")
        if not grader:
            raise EbayError(f"eBay has no grader called '{item['grader']}'. Use the short name, e.g. PSA, BGS, SGC, CGC.")
        if not grade:
            raise EbayError(f"eBay does not accept the grade '{item.get('grade') or ''}' for this card.")
        descriptors = [{"name": GRADER_ID, "values": [grader]}, {"name": GRADE_ID, "values": [grade]}]
        if item.get("cert_number"):
            descriptors.append({"name": CERT_ID, "additionalInfo": item["cert_number"][:30]})
        return GRADED_ENUM, descriptors
    text = (item.get("condition") or "near mint").lower()
    wanted = next((label for word, label in RAW_CONDITIONS if re.search(rf"\b{word}\b", text)), "near mint")
    options = ids.get(CARD_CONDITION_ID, {})
    vid = _pick(options, wanted) or next((v for k, v in options.items() if wanted in k.lower()), None)
    if not vid:
        raise EbayError("Could not find eBay's card-condition choices for this category.")
    return UNGRADED_ENUM, [{"name": CARD_CONDITION_ID, "values": [vid]}]


def aspects(item: dict) -> dict[str, list[str]]:
    tcg = listings.is_tcg(item)
    pairs = [("Game" if tcg else "Sport", item.get("sport")), ("Character" if tcg else "Player/Athlete", item.get("subject")),
             ("Year Manufactured", item.get("year")), ("Set", item.get("brand_set")),
             ("Card Number", item.get("card_number"))]
    return {k: [v.strip()] for k, v in pairs if v and v.strip()}


def photo_urls(item_id: int) -> list[str]:
    """Public links to an item's photos. eBay downloads them, so the portal's public address must be set."""
    base = os.environ.get("CONSIGN_BASE_URL", "").rstrip("/")
    if not base.startswith("https://"):
        raise EbayError("Set CONSIGN_BASE_URL to the portal's https:// address so eBay can fetch the photos.")
    return [f"{base}/p/{p['token']}.{p['ext']}" for p in db.list_photos(item_id)]


def push(client: Ebay, item: dict) -> dict:
    """Create or update the eBay listing for an item. Returns the refreshed item."""
    if item["status"] != "listed":
        raise EbayError("Only items that are for sale can be listed on eBay.")
    urls = photo_urls(item["id"])
    if not urls:
        raise EbayError("eBay needs at least one photo. Add a photo first.")
    pol = {k: db.get_setting(f"ebay_{k}_policy") for k in ("fulfillment", "payment", "return")}
    if not all(pol.values()):
        raise EbayError("Choose your eBay shipping, payment and return policies on the eBay page first.")
    cat = category(item)
    cond, descriptors = condition(item, client.condition_values(cat))
    shop = money.setting("shop_name")
    description = listings._html(listings.description(item, shop))
    client.call("PUT", f"/sell/inventory/v1/inventory_item/{quote(item['sku'])}", json={
        "availability": {"shipToLocationAvailability": {"quantity": item["quantity"]}},
        "condition": cond, "conditionDescriptors": descriptors,
        "product": {"title": listings.title(item), "description": description, "aspects": aspects(item),
                    "imageUrls": urls[:24]}})
    offer = {
        "sku": item["sku"], "marketplaceId": MARKETPLACE, "format": "FIXED_PRICE",
        "availableQuantity": item["quantity"], "categoryId": cat, "listingDescription": description,
        "listingPolicies": {"fulfillmentPolicyId": pol["fulfillment"], "paymentPolicyId": pol["payment"],
                            "returnPolicyId": pol["return"]},
        "pricingSummary": {"price": {"value": money.plain(item["price_cents"]), "currency": "USD"}},
        "merchantLocationKey": LOCATION_KEY,
    }
    offer_id = item.get("ebay_offer_id")
    existing = client.find_offer(item["sku"]) if not offer_id else {"offerId": offer_id}
    if existing:
        offer_id = existing["offerId"]
        client.call("PUT", f"/sell/inventory/v1/offer/{offer_id}", json=offer)
    else:
        offer_id = client.call("POST", "/sell/inventory/v1/offer", json=offer)["offerId"]
    listing_id = item.get("ebay_listing_id")
    if not listing_id:
        listing_id = client.call("POST", f"/sell/inventory/v1/offer/{offer_id}/publish")["listingId"]
    db.update_item(item["id"], ebay_offer_id=offer_id, ebay_listing_id=listing_id, ebay_synced_at=db.now())
    return db.get_item(item["id"])


def end(client: Ebay, item: dict) -> None:
    """End the eBay listing (sold elsewhere, returned to the consignor)."""
    if item.get("ebay_offer_id") and item.get("ebay_listing_id"):
        client.withdraw(item["ebay_offer_id"])
        db.update_item(item["id"], ebay_listing_id=None, ebay_synced_at=db.now())


def listing_url(item: dict) -> str | None:
    if not item.get("ebay_listing_id"):
        return None
    return f"https://www.{'sandbox.' if sandbox() else ''}ebay.com/itm/{item['ebay_listing_id']}"


def sync_orders(client: Ebay, record_sale) -> dict:
    """Record paid eBay orders for consigned items since the last sync.

    ``record_sale(item, price_cents, fees_cents, sold_at, order_id)`` records one unit.
    """
    last = db.get_setting("ebay_last_sync")
    since = last or (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    recorded, skipped = [], []
    for order in client.orders_since(since):
        if order.get("orderPaymentStatus") not in ("PAID", "PARTIALLY_REFUNDED"):
            continue
        if (order.get("cancelStatus") or {}).get("cancelState") == "CANCELED":
            continue
        lines = order.get("lineItems", [])
        order_total = sum(money.to_cents(li["lineItemCost"]["value"]) for li in lines if li.get("lineItemCost")) or 0
        order_fee = money.to_cents((order.get("totalMarketplaceFee") or {}).get("value"))
        for li in lines:
            item = db.get_item_by_sku(li.get("sku") or "")
            if not item:
                continue
            qty = int(li.get("quantity", 1))
            have = sum(1 for s in db.list_sales(item_id=item["id"], include_void=False)
                       if s["external_ref"] == order["orderId"])
            line_total = money.to_cents(li["lineItemCost"]["value"])
            fee = None
            if order_fee is not None and order_total:
                fee = round(order_fee * line_total / order_total)
            for n in range(have, qty):
                if item["status"] not in ("received", "listed"):
                    skipped.append(f"{item['sku']} was already {item['status']} when eBay order {order['orderId']} sold it")
                    break
                unit = line_total // qty + (line_total % qty if n == qty - 1 else 0)
                unit_fee = None if fee is None else fee // qty + (fee % qty if n == qty - 1 else 0)
                record_sale(item, unit, unit_fee, order["creationDate"][:10], order["orderId"])
                recorded.append(item["sku"])
                item = db.get_item(item["id"])
    db.set_setting("ebay_last_sync", started)
    return {"recorded": recorded, "skipped": skipped, "since": since}
