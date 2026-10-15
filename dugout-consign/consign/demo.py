"""Sample data for trying the portal: python -m consign demo"""

from __future__ import annotations

from datetime import date, timedelta

from . import auth, db, ops

PASSWORD = "dugout-demo-2026"


def seed() -> str:
    db.init()
    if db.count_users():
        return "The database already has accounts; demo data was not added."
    owner_id = db.create_user("owner@dugouttx.com", "Shop Owner", "owner", auth.hash_password(PASSWORD))
    owner = db.get_user(owner_id)
    today = date.fromisoformat(db.today())

    def days_ago(n: int) -> str:
        return (today - timedelta(days=n)).isoformat()

    mike = db.create_consignor("Mike Alvarez", email="mike@example.com", phone="281-555-0142",
                               payout_method="zelle", payout_details="281-555-0142")
    jen = db.create_consignor("Jen Park", email="jen@example.com", payout_method="paypal",
                              payout_details="jen@example.com", commission_pct=20.0)
    db.update_consignor(mike, agreement_accepted_at=db.now())
    db.create_user("mike@example.com", "Mike Alvarez", "consignor", auth.hash_password(PASSWORD), consignor_id=mike)

    def add(cid, title, price, **kw):
        iid = db.create_item(consignor_id=cid, title=title, status="submitted", price_cents=price, **kw)
        ops.receive(db.get_item(iid), owner)
        if price:
            ops.list_item(db.get_item(iid), owner)
        return iid

    a = add(mike, "2018 Topps Update Shohei Ohtani RC #US1 PSA 10", 45000, sport="Baseball", year="2018",
            brand_set="Topps Update", card_number="US1", subject="Shohei Ohtani", grader="PSA", grade="10",
            cert_number="81234567", category="Graded card", min_cents=40000)
    b = add(mike, "2003 Topps Chrome LeBron James RC #111", 120000, sport="Basketball", year="2003",
            brand_set="Topps Chrome", card_number="111", subject="LeBron James", grader="BGS", grade="9",
            category="Graded card")
    ball = add(mike, "Signed Jose Altuve baseball, TRISTAR", 18999, sport="Baseball", category="Autograph")
    c = add(jen, "Charizard ex 199/165 Special Illustration Rare", 32500, sport="Pokemon", year="2023",
            brand_set="Scarlet & Violet 151", card_number="199", subject="Charizard", condition="Near Mint",
            category="Raw card")
    add(jen, "Pokémon 151 Booster Bundle (sealed)", 4999, sport="Pokemon", category="Sealed", quantity=3)
    add(jen, "2020 Prizm Justin Herbert RC Silver #325", None, sport="Football", year="2020", brand_set="Prizm",
        card_number="325", subject="Justin Herbert", category="Raw card")
    db.create_item(consignor_id=mike, title="1986 Fleer Michael Jordan #57 (raw)", status="submitted",
                   submitted_by="consignor", asking_cents=300000, sport="Basketball", year="1986")

    ops.record_sale(db.get_item(a), "ebay", 46000, 6150, days_ago(20), owner, "12-34567-89012")
    ops.record_sale(db.get_item(c), "instore", 32500, None, days_ago(2), owner)
    ops.record_sale(db.get_item(b), "whatnot", 115000, None, days_ago(10), owner)
    db.add_transfer("to_consignment", 50000, days_ago(9), "Online transfer", None, owner_id)
    db.add_payout(mike, 30000, "zelle", "ZL-88213", "sent", None, owner_id)
    ops.propose_price(db.get_item(ball), "shop", 15999, "Signed balls are moving at about $160 right now.", owner)
    return (f"Demo data added.\n  Shop owner:  owner@dugouttx.com / {PASSWORD}\n"
            f"  Consignor:   mike@example.com / {PASSWORD}")
