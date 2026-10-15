"""Money in whole cents, the shop's terms, and how a sale is split.

A sale is split in this order:

1. Channel fees (eBay final value fee, Square card processing...) come off the
   sale price first when ``fees_before_commission`` is on (the default), so the
   shop and the consignor share them in proportion. When it is off, the shop
   absorbs the fees and the commission is taken on the full sale price.
2. The commission rate is the item's own rate if set, else the consignor's,
   else the shop's tier for the sale price.
3. Commission is rounded to the cent; the consignor gets the rest, so the two
   shares always add up exactly.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from . import db

CHANNELS = {
    "instore": "In store (Square)",
    "ebay": "eBay",
    "whatnot": "Whatnot",
    "tcgplayer": "TCGplayer",
    "other": "Other",
}

PAYOUT_METHODS = {
    "check": "Check",
    "zelle": "Zelle",
    "paypal": "PayPal",
    "venmo": "Venmo",
    "ach": "Bank transfer (ACH)",
    "cash": "Cash",
    "store_credit": "Store credit",
}

# Defaults the owner can change on the Settings page. Fee rates are estimates
# used to pre-fill a sale; the actual fee on the order always wins.
DEFAULTS = {
    "shop_name": "The Dug Out",
    "commission_tiers": [[0, 30.0], [100, 25.0], [500, 20.0], [1000, 15.0]],   # [from $, percent]
    "fees_before_commission": True,
    "channel_fees": {
        "instore": [2.6, 15],       # percent, cents per order
        "ebay": [13.25, 40],
        "whatnot": [10.9, 30],
        "tcgplayer": [13.25, 30],
        "other": [0.0, 0],
    },
    "consignment_days": 90,          # how long an item stays before it is reviewed for price drop or return
    "hold_days": 7,                  # a sale's money is held this long (returns, chargebacks) before payout
    "min_payout_cents": 0,
    "opening_consignment_cents": 0,  # the consignment bank account balance on the day you started using this
    "shop_account_name": "Shop operating account",
    "consignment_account_name": "Consignment trust account",
    "label_format": "thermal",
    "shop_address": {"addressLine1": "211 Midway St Ste R", "city": "Spring", "stateOrProvince": "TX",
                     "postalCode": "77373"},       # thermal (2.25 x 1.25 in) | avery5160 (30 per sheet)
    "agreement": (
        "1. The Dug Out sells your items in store and online (eBay, Whatnot, TCGplayer) at the agreed price.\n"
        "2. Commission is taken from each sale at the rate shown on your account. Marketplace and card "
        "processing fees come off the sale price before the split.\n"
        "3. Your share is held for the hold period shown on your statement to cover returns, then is "
        "available for payout by your chosen method.\n"
        "4. Items unsold at the end of the consignment period may be repriced with your agreement or "
        "returned to you.\n"
        "5. You confirm the items are yours to sell and are authentic to the best of your knowledge."
    ),
}


def setting(key: str):
    return db.get_setting(key, DEFAULTS[key])


def all_settings() -> dict:
    return {k: setting(k) for k in DEFAULTS}


# --- parsing and formatting ------------------------------------------------

def to_cents(text, allow_negative: bool = False) -> int | None:
    """'$1,250.5' -> 125050. Blank -> None. Raises ValueError on anything else."""
    if text is None:
        return None
    s = re.sub(r"[\s$,]", "", str(text))
    if s == "":
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        raise ValueError(f"'{text}' is not an amount.") from None
    if d < 0 and not allow_negative:
        raise ValueError("Amounts cannot be negative.")
    return int((d * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def fmt(cents: int | None, blank: str = "") -> str:
    if cents is None:
        return blank
    sign = "-" if cents < 0 else ""
    return f"{sign}${abs(cents) / 100:,.2f}"


def plain(cents: int | None) -> str:
    """12345 -> '123.45' for CSV files and form fields."""
    return "" if cents is None else f"{cents / 100:.2f}"


def pct_of(cents: int, pct: float) -> int:
    return int((Decimal(cents) * Decimal(str(pct)) / 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


# --- commission and fees ---------------------------------------------------

def tier_pct(price_cents: int, tiers: list | None = None) -> float:
    tiers = sorted(tiers if tiers is not None else setting("commission_tiers"), key=lambda t: t[0])
    pct = tiers[0][1] if tiers else 0.0
    for start, rate in tiers:
        if price_cents >= round(start * 100):
            pct = rate
    return float(pct)


def commission_pct(item: dict, price_cents: int) -> float:
    if item.get("commission_pct") is not None:
        return float(item["commission_pct"])
    if item.get("consignor_pct") is not None:
        return float(item["consignor_pct"])
    return tier_pct(price_cents)


def estimate_fees(channel: str, price_cents: int) -> int:
    pct, fixed = setting("channel_fees").get(channel, [0.0, 0])
    return pct_of(price_cents, pct) + int(fixed) if price_cents else 0


def split(price_cents: int, fees_cents: int, pct: float, fees_before: bool | None = None) -> dict:
    """Return the shop's commission and the consignor's share of one sale."""
    if fees_before is None:
        fees_before = setting("fees_before_commission")
    if fees_before:
        base = max(price_cents - fees_cents, 0)
        commission = pct_of(base, pct)
        consignor = base - commission
    else:
        commission = pct_of(price_cents, pct)
        consignor = price_cents - commission
    return {"commission_cents": commission, "consignor_cents": consignor, "pct": pct}


def parse_tiers(text: str) -> list[list]:
    """'0:30, 100:25, 500:20' -> [[0, 30.0], [100, 25.0], [500, 20.0]]"""
    tiers = []
    for part in re.split(r"[,\n]+", text or ""):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"\$?([\d.]+)\s*[:=]\s*([\d.]+)\s*%?", part)
        if not m:
            raise ValueError(f"Could not read the tier '{part}'. Write it as price:percent, e.g. 100:25.")
        rate = float(m.group(2))
        if not 0 <= rate <= 100:
            raise ValueError("Commission must be between 0 and 100 percent.")
        tiers.append([float(m.group(1)) if "." in m.group(1) else int(m.group(1)), rate])
    if not tiers:
        raise ValueError("Enter at least one tier, e.g. 0:30.")
    return sorted(tiers, key=lambda t: t[0])


def tiers_text(tiers: list) -> str:
    return ", ".join(f"{a}:{b:g}" for a, b in tiers)
