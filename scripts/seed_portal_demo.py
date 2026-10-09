"""Create a sample client with a working vendor certificate portal, for demos.

    python scripts/seed_portal_demo.py && python -m certapp serve

All names, numbers, rules and certificates are made up. The rules are simplified
examples to show how the portal behaves, not tax advice.
"""

from __future__ import annotations

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from certapp import auth, db, portal  # noqa: E402

CLIENT = "Summit Industrial Holdings, Inc. (demo)"
PASSWORD = "demo-password-1"


def sample_pdf(lines: list[str]) -> bytes:
    """A one-page PDF showing the given lines of text (no extra libraries needed)."""
    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    text = ["BT", "/F1 20 Tf", "72 720 Td", f"({esc(lines[0])}) Tj", "/F1 12 Tf"]
    for line in lines[1:]:
        text += ["0 -22 Td", f"({esc(line)}) Tj"]
    text.append("ET")
    stream = "\n".join(text).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for n, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % n + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def main() -> None:
    if os.environ.get("CERTAPP_ENV", "").lower() == "production":
        sys.exit("Refusing to create demo data and shared-password accounts on a production server.")
    db.init()
    eid = db.create_engagement(CLIENT, None)

    ent = {key: portal.add("entity", eid, name=name, home_state=st, notes=note) for key, name, st, note in [
        ("mfg", "Summit Manufacturing LLC", "TX", "Texas sales tax permit 3-2045-118-7731 (sample)"),
        ("dist", "Summit Distribution LLC", "CA", "CDTFA seller's permit SR KH 102-555123 (sample)"),
        ("cons", "Summit Construction Services LLC", "FL", "General contractor for owner projects"),
    ]}
    projects = {}
    for key, entity, name, number, st, ptype in [
        ("houston", "mfg", "Houston Plant Expansion", "P-1042", "TX", "manufacturing"),
        ("dallas", "mfg", "Dallas Regional Office", "A-0150", "TX", "administrative"),
        ("fresno", "dist", "Fresno Distribution Center", "D-2207", "CA", "resale"),
        ("tampa", "cons", "Tampa Federal Clinic Build-Out", "C-3310", "FL", "exempt_owner"),
    ]:
        projects[key] = portal.add("project", eid, entity_id=ent[entity], name=name, number=number,
                                   state=st, project_type=ptype)

    cat = {key: portal.add("category", eid, name=name, examples=ex) for key, name, ex in [
        ("equip", "Manufacturing machinery and equipment", "CNC machines, presses, conveyors, robotic cells"),
        ("raw", "Raw materials and component parts", "Steel coil, resin, fasteners that become part of finished products"),
        ("resale", "Goods bought for resale", "Finished products resold to customers without change"),
        ("constr", "Construction materials", "Conduit, lumber, drywall, concrete, installed HVAC units"),
        ("office", "Office supplies and furniture", "Paper, desks, chairs, printers"),
        ("software", "Software and cloud services", "Software licenses, SaaS subscriptions"),
        ("repair", "Repair and maintenance services", "On-site repair labor, service contracts"),
    ]}

    folder = db.files_dir() / "portal" / str(eid)
    folder.mkdir(parents=True, exist_ok=True)
    expires = (date.today() + timedelta(days=700)).isoformat()
    for key, entity, st, ctype, label, form in [
        ("tx_mfg", "mfg", "TX", "manufacturing", "Texas Manufacturing Exemption Certificate", "Form 01-339 (back)"),
        ("tx_resale", "mfg", "TX", "resale", "Texas Resale Certificate", "Form 01-339 (front)"),
        ("ca_resale", "dist", "CA", "resale", "California Resale Certificate", "CDTFA-230"),
    ]:
        name = [e for e in portal.listing("entity", eid) if e["id"] == ent[entity]][0]["name"]
        path = folder / f"{key}.pdf"
        path.write_bytes(sample_pdf([
            f"SAMPLE - {label}", f"{form}", "", f"Purchaser: {name}", f"State: {st}",
            "Signed by: Jordan Avery, Director of Tax (sample signature)", f"Valid through: {expires}",
            "", "Demonstration document only. Not a valid certificate."]))
        portal.add("certificate", eid, entity_id=ent[entity], state=st, cert_type=ctype, label=label,
                   filename=f"{label}.pdf", storage_path=str(path), expires=expires)

    for c, st, ptype, outcome, ctype, note in [
        ("equip", "TX", "manufacturing", "exempt", "manufacturing",
         "Machinery used directly in manufacturing is exempt in Texas."),
        ("equip", "CA", "*", "review", None,
         "California offers only a partial manufacturing exemption. Contact us to confirm the rate."),
        ("equip", "*", "administrative", "taxable", None, "Equipment for office use is taxable."),
        ("raw", "*", "manufacturing", "exempt", "resale",
         "Materials that become part of products for sale are bought for resale."),
        ("resale", "*", "resale", "exempt", "resale", "Goods bought for resale."),
        ("constr", "TX", "*", "taxable", None, "Materials installed in real property are taxable to the buyer."),
        ("constr", "FL", "exempt_owner", "review", None,
         "Government-owned project: the exemption depends on the contract. Contact us before invoicing."),
        ("office", "*", "*", "taxable", None, "Office supplies and furniture are taxable."),
        ("software", "*", "*", "review", None, "Software and cloud services are taxed differently by state."),
        ("repair", "TX", "*", "taxable", None, "Repair services are taxable in Texas."),
    ]:
        portal.add("rule", eid, category_id=cat[c], state=st, project_type=ptype, outcome=outcome,
                   cert_type=ctype, note=note)

    portal.save_contact(eid, "Summit Tax Department", "salestax@summit-demo.test", "(555) 010-2400",
                        "Please contact our tax team before invoicing so we can confirm the right treatment.")

    firm = auth.firm_domains()[0]
    accounts = [(f"demo.admin@{firm}", "Demo Administrator", "firm_admin", None),
                ("vendor@acme-supply.test", "Acme Industrial Supply", "vendor", eid),
                ("vendor@gulf-electric.test", "Gulf Coast Electric", "vendor", eid)]
    for email, name, role, client in accounts:
        if not db.get_user_by_email(email):
            db.create_user(email, name, role, "password", engagement_id=client,
                           password_hash=auth.hash_password(PASSWORD))

    # Two example requests so the log is not empty.
    acme = db.get_user_by_email("vendor@acme-supply.test")
    cats = {c["id"]: c for c in portal.listing("category", eid)}
    rules, certs = portal.listing("rule", eid), portal.listing("certificate", eid)
    for project_key, ref, items in [
        ("houston", "Q-88213", [("Haas VF-4 vertical machining center", "equip"), ("Steel bar stock, 4140", "raw")]),
        ("tampa", "Q-88240", [("2 in. EMT conduit, 500 ft", "constr"), ("Breaker panels", "constr")]),
    ]:
        project = portal.get("project", eid, projects[project_key])
        lines = [{"description": d, "category_id": cat[k]} for d, k in items]
        decision = portal.decide(project, lines, cats, rules, certs)
        portal.record_request(eid, acme, project["id"], ref, lines, decision)

    print(f"Created '{CLIENT}' with a vendor portal (client #{eid}).")
    print(f"Sign-ins (password for all: {PASSWORD}):")
    for email, _, role, _ in accounts:
        print(f"  {auth.ROLES[role]:<20} {email}")
    print("Firm view: open the client and click 'Vendor portal'. Vendor view: sign in as a vendor.")


if __name__ == "__main__":
    main()
