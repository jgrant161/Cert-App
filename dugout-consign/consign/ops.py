"""What happens to an item over its life, and the money moves that go with it.

submitted -> received -> listed -> sold
                    \\-> returned (to the consignor)       submitted -> declined

Every function here is used by both the web pages and the Square sync, so a
sale recorded by hand and one read from Square are split and stocked alike.
Square calls never block the shop's own records: if Square is unreachable the
record is kept and a warning is returned for the page to show.
"""

from __future__ import annotations

from datetime import date, timedelta

from . import db, ebay, money, square


class OpError(ValueError):
    pass


def _square(sq, fn, *args) -> str | None:
    if sq is None:
        return None
    try:
        fn(sq, *args)
    except Exception as e:     # noqa: BLE001  - any Square failure becomes a warning
        return f"Saved here, but Square was not updated: {e}"
    return None


def _ebay(eb, fn, *args) -> str | None:
    if eb is None:
        return None
    try:
        fn(eb, *args)
    except Exception as e:     # noqa: BLE001  - any eBay failure becomes a warning
        return f"Saved here, but eBay was not updated: {e}"
    return None


def _join(*warnings) -> str | None:
    return " ".join(w for w in warnings if w) or None


def _resync(item: dict, sq, eb) -> str | None:
    """Send a changed price or quantity to wherever the item is already listed."""
    w1 = _square(sq, square.push_item, item) if item["square_item_id"] else None
    w2 = _ebay(eb, ebay.push, item) if item["ebay_listing_id"] and item["status"] == "listed" else None
    return _join(w1, w2)


def receive(item: dict, user: dict | None, location: str | None = None) -> None:
    if item["status"] not in ("submitted", "received"):
        raise OpError(f"This item is {item['status']}; it cannot be checked in.")
    days = int(money.setting("consignment_days"))
    today = db.today()
    db.update_item(item["id"], status="received", received_at=item["received_at"] or today,
                   expires_at=item["expires_at"] or (date.fromisoformat(today) + timedelta(days=days)).isoformat(),
                   location=location if location is not None else item["location"])
    db.audit(user, "item_received", item["consignor_id"], item["id"])


def list_item(item: dict, user: dict | None, sq=None) -> str | None:
    if item["status"] not in ("received", "listed"):
        raise OpError("Check the item in before listing it.")
    if not item["price_cents"]:
        raise OpError("Set a price before listing the item.")
    db.update_item(item["id"], status="listed", listed_at=item["listed_at"] or db.today())
    db.audit(user, "item_listed", item["consignor_id"], item["id"], money.fmt(item["price_cents"]))
    return _square(sq, square.push_item, db.get_item(item["id"]))


def set_price(item: dict, cents: int, user: dict | None, sq=None, eb=None) -> str | None:
    if cents <= 0:
        raise OpError("The price must be more than $0.")
    db.update_item(item["id"], price_cents=cents)
    db.audit(user, "price_set", item["consignor_id"], item["id"], money.fmt(cents))
    return _resync(db.get_item(item["id"]), sq, eb)


def propose_price(item: dict, by: str, cents: int, note: str | None, user: dict | None) -> int:
    if item["status"] in ("sold", "returned", "declined"):
        raise OpError("This item is closed.")
    if cents <= 0:
        raise OpError("The price must be more than $0.")
    pid = db.add_proposal(item["id"], by, user["id"] if user else None, cents, note)
    db.audit(user, "price_proposed", item["consignor_id"], item["id"], f"{by}: {money.fmt(cents)}")
    return pid


def answer_proposal(item: dict, proposal: dict, accept: bool, user: dict | None, sq=None, eb=None) -> str | None:
    if proposal["status"] != "pending":
        raise OpError("That price proposal has already been answered.")
    db.decide_proposal(proposal["id"], "accepted" if accept else "declined")
    db.audit(user, "price_accepted" if accept else "price_declined", item["consignor_id"], item["id"],
             money.fmt(proposal["price_cents"]))
    if accept:
        fields = {"price_cents": proposal["price_cents"]}
        if proposal["proposed_by"] == "shop" and item["min_cents"] and proposal["price_cents"] < item["min_cents"]:
            fields["min_cents"] = proposal["price_cents"]     # the consignor agreed to go below their floor
        db.update_item(item["id"], **fields)
        return _resync(db.get_item(item["id"]), sq, eb)
    return None


def record_sale(item: dict, channel: str, price_cents: int, fees_cents: int | None, sold_at: str,
                user: dict | None, external_ref: str | None = None, square_order_id: str | None = None,
                sq=None, eb=None, allow_below_floor: bool = False) -> tuple[int, str | None]:
    """Record one unit sold. Returns (sale id, warning or None)."""
    if channel not in money.CHANNELS:
        raise OpError("Choose where the item sold.")
    if item["status"] not in ("received", "listed"):
        raise OpError(f"This item is {item['status']}; it cannot be sold.")
    if price_cents <= 0:
        raise OpError("Enter the sale price.")
    if item["min_cents"] and price_cents < item["min_cents"] and not allow_below_floor:
        raise OpError(f"That is below the consignor's floor of {money.fmt(item['min_cents'])}. "
                      "Tick 'Below floor, approved by consignor' to record it anyway.")
    if fees_cents is None:
        fees_cents = money.estimate_fees(channel, price_cents)
    pct = money.commission_pct(item, price_cents)
    parts = money.split(price_cents, fees_cents, pct)
    sid = db.add_sale(item_id=item["id"], consignor_id=item["consignor_id"], channel=channel, sold_at=sold_at,
                      price_cents=price_cents, fees_cents=fees_cents, commission_pct=pct,
                      commission_cents=parts["commission_cents"], consignor_cents=parts["consignor_cents"],
                      external_ref=external_ref, square_order_id=square_order_id,
                      user_id=user["id"] if user else None)
    left = max(item["quantity"] - 1, 0)
    fields = {"quantity": left}
    if left == 0:
        fields.update(status="sold", sold_at=sold_at, closed_at=db.today())
    db.update_item(item["id"], **fields)
    db.audit(user, "sale_recorded", item["consignor_id"], item["id"],
             f"{money.CHANNELS[channel]} {money.fmt(price_cents)}; consignor {money.fmt(parts['consignor_cents'])}")
    fresh = db.get_item(item["id"])
    warning = warning_ebay = None
    # A Square register sale already took the stock down in Square; anything else must.
    if (channel != "instore" or not square_order_id) and fresh["square_variation_id"]:
        warning = _square(sq, lambda c, v, q: c.set_stock(v, q), fresh["square_variation_id"],
                          left if fresh["status"] == "listed" else 0)
    # Likewise eBay counts its own sales; a sale anywhere else ends or shrinks the eBay listing.
    if channel != "ebay" and fresh["ebay_listing_id"]:
        warning_ebay = _ebay(eb, ebay.end if left == 0 else ebay.push, fresh)
    return sid, _join(warning, warning_ebay)


def void_sale(sale: dict, reason: str, user: dict | None, sq=None) -> str | None:
    if sale["voided_at"]:
        raise OpError("This sale is already void.")
    if not reason.strip():
        raise OpError("Say why the sale is being voided (return, cancelled order...).")
    db.void_sale(sale["id"], reason.strip())
    item = db.get_item(sale["item_id"])
    db.update_item(item["id"], quantity=item["quantity"] + 1,
                   status="listed" if item["status"] == "sold" else item["status"], sold_at=None, closed_at=None)
    db.audit(user, "sale_voided", item["consignor_id"], item["id"], reason.strip())
    fresh = db.get_item(item["id"])
    if fresh["square_item_id"]:
        return _square(sq, square.push_item, fresh)
    return None


def close_item(item: dict, status: str, user: dict | None, sq=None, eb=None) -> str | None:
    """Return an item to its consignor, or decline a submission."""
    if status == "returned" and item["status"] not in ("received", "listed"):
        raise OpError("Only items in the shop can be returned.")
    if status == "declined" and item["status"] != "submitted":
        raise OpError("Only submissions can be declined.")
    db.update_item(item["id"], status=status, closed_at=db.today())
    db.audit(user, f"item_{status}", item["consignor_id"], item["id"])
    return _join(_square(sq, square.zero_stock, item), _ebay(eb, ebay.end, item) if item["ebay_listing_id"] else None)


def relist(item: dict, user: dict | None) -> None:
    if item["status"] != "returned":
        raise OpError("Only returned items can be brought back.")
    db.update_item(item["id"], status="received", closed_at=None, received_at=db.today(),
                   expires_at=(date.fromisoformat(db.today())
                               + timedelta(days=int(money.setting("consignment_days")))).isoformat())
    db.audit(user, "item_rereceived", item["consignor_id"], item["id"])


# --- payouts ---------------------------------------------------------------

def create_payout(consignor: dict, amount_cents: int, method: str, reference: str | None, sent: bool,
                  memo: str | None, user: dict | None, available_cents: int) -> int:
    if method not in money.PAYOUT_METHODS:
        raise OpError("Choose how the consignor is being paid.")
    if amount_cents <= 0:
        raise OpError("Enter the payout amount.")
    if amount_cents > available_cents:
        raise OpError(f"{consignor['name']} has {money.fmt(available_cents)} available to pay out "
                      "(money from recent sales is held for the hold period).")
    pid = db.add_payout(consignor["id"], amount_cents, method, reference, "sent" if sent else "pending", memo,
                        user["id"] if user else None)
    db.audit(user, "payout_created", consignor["id"], detail=f"{money.fmt(amount_cents)} {method}"
                                                              f"{' sent' if sent else ' pending'}")
    return pid


def mark_payout_sent(payout: dict, reference: str | None, user: dict | None) -> None:
    if payout["status"] != "pending":
        raise OpError("Only pending payouts can be marked sent.")
    db.update_payout(payout["id"], status="sent", sent_at=db.today(), reference=reference or payout["reference"])
    db.audit(user, "payout_sent", payout["consignor_id"], detail=money.fmt(payout["amount_cents"]))


def void_payout(payout: dict, user: dict | None) -> None:
    if payout["status"] == "void":
        raise OpError("Already void.")
    db.update_payout(payout["id"], status="void")
    db.audit(user, "payout_voided", payout["consignor_id"], detail=money.fmt(payout["amount_cents"]))
