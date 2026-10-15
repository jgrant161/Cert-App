"""Listing copy and bulk-upload files for eBay, Whatnot and TCGplayer.

Each marketplace takes a spreadsheet upload, so the app builds one file per
marketplace from the selected items, photos included as public links. The
column layouts follow each marketplace's upload template as published; they
change from time to time, so the first upload of a new file should be a
single item, and the column names can be adjusted here in one place.
"""

from __future__ import annotations

import csv
import io

from . import money

EBAY_TITLE_MAX = 80
EBAY_SPORTS_CARDS = "261328"      # Sports Trading Card Singles
EBAY_CCG_CARDS = "183454"         # CCG Individual Cards (Pokémon and other trading card games)
# Your eBay business policy names (Seller Hub > Account > Business policies). Blank leaves them for eBay to ask.
EBAY_SHIPPING_POLICY = ""
EBAY_RETURN_POLICY = ""
EBAY_PAYMENT_POLICY = ""
TCG_GAMES = {"pokemon", "pokémon", "magic", "mtg", "yu-gi-oh", "yugioh", "lorcana", "one piece", "flesh and blood"}


def is_tcg(item: dict) -> bool:
    return (item.get("sport") or "").strip().lower() in TCG_GAMES or (item.get("category") or "").lower().startswith(
        ("pokemon", "pokémon", "tcg"))


def title(item: dict, limit: int = EBAY_TITLE_MAX) -> str:
    """'2018 Topps Update #US250 Shohei Ohtani RC PSA 10', trimmed to the limit."""
    parts = [item.get("year"), item.get("brand_set"),
             f"#{item['card_number'].lstrip('#')}" if item.get("card_number") else None,
             item.get("subject"),
             "RC" if " rc" in f" {item['title'].lower()} " or "rookie" in item["title"].lower() else None,
             f"{item['grader']} {item.get('grade') or ''}".strip() if item.get("grader") else None]
    built = " ".join(p.strip() for p in parts if p and p.strip())
    text = built if len(built) >= 12 else item["title"]
    if len(text) > limit:
        text = text[:limit].rsplit(" ", 1)[0]
    return text


def description(item: dict, shop_name: str) -> str:
    lines = [item["title"], ""]
    facts = [("Year", item.get("year")), ("Set", item.get("brand_set")), ("Card #", item.get("card_number")),
             ("Player / character", item.get("subject")), ("Grader", item.get("grader")),
             ("Grade", item.get("grade")), ("Cert #", item.get("cert_number")), ("Condition", item.get("condition"))]
    lines += [f"{k}: {v}" for k, v in facts if v]
    if item.get("description"):
        lines += ["", item["description"]]
    lines += ["", "Pictured is the exact item you will receive.",
              f"Ships from {shop_name}, Old Town Spring, TX. Item {item['sku']}."]
    return "\n".join(lines)


def _html(text: str) -> str:
    from html import escape
    return "<p>" + "</p><p>".join(escape(p).replace("\n", "<br>") for p in text.split("\n\n")) + "</p>"


def ebay(items: list[dict], photo_urls: dict[int, list[str]], shop_name: str) -> tuple[list[str], list[list]]:
    """eBay Seller Hub > Reports > Uploads, 'Add' rows (File Exchange format)."""
    header = ["*Action(SiteID=US|Country=US|Currency=USD|Version=1193)", "CustomLabel", "*Category", "*Title",
              "*ConditionID", "C:Graded", "C:Professional Grader", "C:Grade", "C:Certification Number",
              "C:Sport", "C:Game", "C:Player/Athlete", "C:Character", "C:Year Manufactured", "C:Set",
              "C:Card Number", "PicURL", "*Description", "*Format", "*Duration", "*StartPrice", "*Quantity",
              "Location", "ShippingProfileName", "ReturnProfileName", "PaymentProfileName"]
    rows = []
    for i in items:
        tcg = is_tcg(i)
        graded = bool(i.get("grader"))
        rows.append([
            "Add", i["sku"], EBAY_CCG_CARDS if tcg else EBAY_SPORTS_CARDS, title(i),
            "2750" if graded else "4000",          # 2750 Graded, 4000 Ungraded (trading card categories)
            "Yes" if graded else "No", i.get("grader") or "", i.get("grade") or "", i.get("cert_number") or "",
            "" if tcg else (i.get("sport") or ""), (i.get("sport") or "") if tcg else "",
            "" if tcg else (i.get("subject") or ""), (i.get("subject") or "") if tcg else "",
            i.get("year") or "", i.get("brand_set") or "", i.get("card_number") or "",
            "|".join(photo_urls.get(i["id"], [])[:24]), _html(description(i, shop_name)),
            "FixedPrice", "GTC", money.plain(i["price_cents"]), i["quantity"], "Spring, TX",
            EBAY_SHIPPING_POLICY, EBAY_RETURN_POLICY, EBAY_PAYMENT_POLICY,
        ])
    return header, rows


def whatnot(items: list[dict], photo_urls: dict[int, list[str]], shop_name: str) -> tuple[list[str], list[list]]:
    """Whatnot Seller Hub bulk listing upload (Buy It Now)."""
    header = ["Category", "Sub Category", "Title", "Description", "Quantity", "Type", "Price", "Shipping Profile",
              "Offerable", "Hazmat", "Condition", "Cost Per Item", "SKU",
              *[f"Image URL {n}" for n in range(1, 9)]]
    rows = []
    for i in items:
        urls = photo_urls.get(i["id"], [])[:8]
        rows.append([
            "Trading Card Games" if is_tcg(i) else "Sports Cards", i.get("sport") or "", title(i, 80),
            description(i, shop_name), i["quantity"], "Buy it Now", money.plain(i["price_cents"]), "",
            "TRUE", "Not Hazmat", "Graded" if i.get("grader") else (i.get("condition") or "Near Mint"), "",
            i["sku"], *urls, *[""] * (8 - len(urls)),
        ])
    return header, rows


def tcgplayer(items: list[dict], photo_urls: dict[int, list[str]], shop_name: str) -> tuple[list[str], list[list]]:
    """TCGplayer Seller Portal pricing import. Rows need the TCGplayer product id."""
    header = ["TCGplayer Id", "Product Line", "Set Name", "Product Name", "Number", "Condition",
              "Add to Quantity", "TCG Marketplace Price", "Photo URL"]
    rows = [[i.get("tcgplayer_id") or "", i.get("sport") or "", i.get("brand_set") or "", i.get("subject") or i["title"],
             i.get("card_number") or "", i.get("condition") or "Near Mint", i["quantity"],
             money.plain(i["price_cents"]), (photo_urls.get(i["id"]) or [""])[0]] for i in items]
    return header, rows


BUILDERS = {"ebay": ebay, "whatnot": whatnot, "tcgplayer": tcgplayer}


def to_csv(header: list[str], rows: list[list]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    w.writerows(rows)
    return "﻿" + buf.getvalue()     # byte-order mark so Excel opens accents correctly
