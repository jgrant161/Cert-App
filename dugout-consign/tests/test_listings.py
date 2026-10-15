from conftest import add_item

from consign import db, listings


def test_title_is_built_from_fields_and_fits_ebay():
    item = {"title": "x", "year": "2018", "brand_set": "Topps Update", "card_number": "#US1",
            "subject": "Shohei Ohtani", "grader": "PSA", "grade": "10", "sku": "DO000001"}
    assert listings.title(item) == "2018 Topps Update #US1 Shohei Ohtani PSA 10"
    long = dict(item, brand_set="Topps Update Series Chrome Refractor Gold Wave Parallel Limited Edition Special")
    assert len(listings.title(long)) <= 80
    assert listings.title({"title": "Signed ball", "sku": "DO1"}) == "Signed ball"


def test_marketplace_files():
    cid = db.create_consignor("Jen")
    card = add_item(cid, title="Charizard", price=32500, sport="Pokemon", subject="Charizard", tcgplayer_id="12345")
    slab = add_item(cid, title="Ohtani", price=45000, sport="Baseball", subject="Shohei Ohtani", grader="PSA",
                    grade="10", cert_number="81234567")
    items = [db.get_item(card["id"]), db.get_item(slab["id"])]
    urls = {card["id"]: ["https://x/p/a.jpg", "https://x/p/b.jpg"]}
    header, rows = listings.ebay(items, urls, "The Dug Out")
    row = dict(zip(header, rows[0]))
    assert row["*Category"] == listings.EBAY_CCG_CARDS and row["C:Character"] == "Charizard" and row["C:Sport"] == ""
    assert row["PicURL"] == "https://x/p/a.jpg|https://x/p/b.jpg" and row["*StartPrice"] == "325.00"
    row = dict(zip(header, rows[1]))
    assert row["*Category"] == listings.EBAY_SPORTS_CARDS and row["*ConditionID"] == "2750"
    assert row["C:Sport"] == "Baseball" and row["C:Player/Athlete"] == "Shohei Ohtani" and row["C:Certification Number"] == "81234567"
    header, rows = listings.whatnot(items, urls, "The Dug Out")
    assert len(rows[0]) == len(header) and rows[0][header.index("Image URL 2")] == "https://x/p/b.jpg"
    header, rows = listings.tcgplayer(items, urls, "The Dug Out")
    assert rows[0][0] == "12345" and rows[0][header.index("TCG Marketplace Price")] == "325.00"
    assert listings.to_csv(header, rows).startswith("﻿TCGplayer Id,")
