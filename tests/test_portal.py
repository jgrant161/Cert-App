"""Vendor certificate portal: the decision rules and who can see what."""

import re
from datetime import date

import pytest
from fastapi.testclient import TestClient

from certapp import db, portal
from certapp.web.app import create_app

from conftest import make_user, sign_in

PDF = b"%PDF-1.4 sample certificate"


# --- decide() ----------------------------------------------------------------

def rule(cat, state, ptype, outcome, cert_type=None, note=None, rid=0):
    return {"id": rid, "category_id": cat, "state": state, "project_type": ptype, "outcome": outcome,
            "cert_type": cert_type, "note": note}


CATS = {1: {"id": 1, "name": "Equipment"}, 2: {"id": 2, "name": "Office"}}
PROJECT = {"id": 9, "entity_id": 5, "state": "TX", "project_type": "manufacturing"}
CERT = {"id": 70, "entity_id": 5, "state": "TX", "cert_type": "manufacturing", "label": "TX mfg", "expires": None}


def one(rules, certs=(CERT,), project=PROJECT, cat=1, today=None):
    d = portal.decide(project, [{"description": "item", "category_id": cat}], CATS, list(rules), list(certs),
                      today=today)
    return d, d.items[0]


def test_most_specific_rule_wins():
    rules = [rule(1, "*", "*", "taxable"), rule(1, "*", "manufacturing", "review"),
             rule(1, "TX", "*", "exempt", "manufacturing")]
    _, item = one(rules)
    assert item.outcome == "exempt"                       # exact state beats exact project type
    _, item = one(rules[:2])
    assert item.outcome == "review"                       # exact project type beats any/any
    _, item = one(rules[:1])
    assert item.outcome == "taxable"


def test_rules_for_other_states_or_types_do_not_apply():
    _, item = one([rule(1, "CA", "*", "exempt", "manufacturing"), rule(1, "*", "resale", "taxable")])
    assert item.outcome == "review" and "No rule" in item.reason


def test_exempt_hands_out_the_certificate():
    d, item = one([rule(1, "TX", "*", "exempt", "manufacturing")])
    assert d.status == "exempt" and item.certificate_id == 70
    assert d.certificates == [{"id": 70, "label": "TX mfg", "state": "TX", "cert_type": "manufacturing"}]


def test_exempt_without_a_certificate_goes_to_review():
    other_entity = {**CERT, "entity_id": 6}
    other_state = {**CERT, "state": "CA"}
    wrong_type = {**CERT, "cert_type": "resale"}
    expired = {**CERT, "expires": "2020-01-01"}
    for certs in ([], [other_entity], [other_state], [wrong_type], [expired]):
        d, item = one([rule(1, "TX", "*", "exempt", "manufacturing")], certs=certs, today=date(2026, 1, 1))
        assert item.outcome == "review" and d.certificates == [], certs


def test_state_certificate_preferred_over_multistate():
    multi = {**CERT, "id": 71, "state": "*"}
    _, item = one([rule(1, "TX", "*", "exempt", "manufacturing")], certs=[multi, CERT])
    assert item.certificate_id == 70
    _, item = one([rule(1, "TX", "*", "exempt", "manufacturing")], certs=[multi])
    assert item.certificate_id == 71


def test_overall_status():
    rules = [rule(1, "*", "*", "exempt", "manufacturing"), rule(2, "*", "*", "taxable")]
    both = portal.decide(PROJECT, [{"description": "a", "category_id": 1}, {"description": "b", "category_id": 2}],
                         CATS, rules, [CERT])
    assert both.status == "mixed"
    with_review = portal.decide(PROJECT, [{"description": "a", "category_id": 1},
                                          {"description": "b", "category_id": 99}], CATS, rules, [CERT])
    assert with_review.status == "review"


# --- the web portal ----------------------------------------------------------

def setup_portal(name: str) -> dict:
    eid = db.create_engagement(name)
    ent = portal.add("entity", eid, name=f"{name} Mfg", home_state="TX", notes="")
    proj = portal.add("project", eid, entity_id=ent, name=f"{name} Plant", number="P-1", state="TX",
                      project_type="manufacturing")
    equip = portal.add("category", eid, name="Equipment", examples="")
    office = portal.add("category", eid, name="Office", examples="")
    portal.add("rule", eid, category_id=equip, state="TX", project_type="*", outcome="exempt",
               cert_type="manufacturing", note="Exempt in TX")
    portal.add("rule", eid, category_id=office, state="*", project_type="*", outcome="taxable",
               cert_type=None, note="")
    folder = db.files_dir() / "portal" / str(eid)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "c.pdf"
    path.write_bytes(PDF + name.encode())
    cert = portal.add("certificate", eid, entity_id=ent, state="TX", cert_type="manufacturing",
                      label=f"{name} TX certificate", filename="cert.pdf", storage_path=str(path), expires=None)
    portal.save_contact(eid, f"{name} Tax", f"tax@{name.lower()}.test", "", "Call us")
    return {"eid": eid, "project": proj, "equip": equip, "office": office, "cert": cert}


@pytest.fixture
def world():
    a, b = setup_portal("Alpha"), setup_portal("Beta")
    users = {
        "admin": make_user("firm_admin", email="admin@firm.com"),
        "staff_a": make_user("firm_staff", email="staff.a@firm.com"),
        "staff_none": make_user("firm_staff", email="staff.none@firm.com"),
        "client_admin_a": make_user("client_admin", a["eid"], email="boss@a.com"),
        "client_user_a": make_user("client_user", a["eid"], email="clerk@a.com"),
        "vendor_a": make_user("vendor", a["eid"], email="vendor1@supply.test"),
        "vendor_a2": make_user("vendor", a["eid"], email="vendor2@supply.test"),
        "vendor_b": make_user("vendor", b["eid"], email="vendor@beta-supply.test"),
    }
    db.assign_staff(a["eid"], users["staff_a"]["id"])
    return {"a": a, "b": b, "users": users}


def as_user(world, key):
    return sign_in(TestClient(create_app()), world["users"][key])


def submit(client, p, items, eid=None):
    data = {"project_id": str(p["project"]), "reference": "PO-1",
            "description": [d for d, _ in items], "category_id": [str(c) for _, c in items]}
    if eid:
        data["eid"] = str(eid)
    return client.post("/portal/check", data=data)


def request_id(response) -> int:
    return int(re.search(r"/portal/r/(\d+)$", str(response.url)).group(1))


def test_vendor_gets_certificate_and_can_download_it(world):
    a = world["a"]
    vendor = as_user(world, "vendor_a")
    assert vendor.get("/").url.path == "/portal"
    r = submit(vendor, a, [("CNC machine", a["equip"])])
    assert r.status_code == 200 and "Your certificate is ready" in r.text
    rid = request_id(r)
    pdf = vendor.get(f"/portal/r/{rid}/cert/{a['cert']}")
    assert pdf.status_code == 200 and pdf.content == PDF + b"Alpha"
    assert any(e["action"] == "certificate_issued" for e in db.list_audit())


def test_vendor_sent_to_contact_when_not_exempt(world):
    a = world["a"]
    vendor = as_user(world, "vendor_a")
    r = submit(vendor, a, [("Desk", a["office"])])
    assert "Taxable" in r.text and "tax@alpha.test" in r.text
    rid = request_id(r)
    assert vendor.get(f"/portal/r/{rid}/cert/{a['cert']}").status_code == 404   # not granted by this request


def test_vendor_form_rejects_another_clients_project_or_category(world):
    a, b = world["a"], world["b"]
    vendor = as_user(world, "vendor_a")
    r = submit(vendor, b, [("CNC machine", b["equip"])])
    assert r.status_code == 400
    r = submit(vendor, a, [("CNC machine", b["equip"])])
    assert r.status_code == 400
    r = submit(vendor, a, [("CNC machine", a["equip"])], eid=b["eid"])     # a vendor cannot pick the client
    assert r.status_code == 200 and "Alpha TX certificate" in r.text
    assert not portal.requests(b["eid"])


def test_vendor_sees_only_own_requests(world):
    a, b = world["a"], world["b"]
    rid = request_id(submit(as_user(world, "vendor_a"), a, [("CNC", a["equip"])]))
    rid_b = request_id(submit(as_user(world, "vendor_b"), b, [("CNC", b["equip"])]))
    other = as_user(world, "vendor_a2")
    assert other.get(f"/portal/r/{rid}").status_code == 404
    assert other.get(f"/portal/r/{rid}/cert/{a['cert']}").status_code == 404
    assert "PO-1" not in other.get("/portal").text
    mine = as_user(world, "vendor_a")
    assert mine.get(f"/portal/r/{rid_b}").status_code == 404
    assert mine.get(f"/portal/r/{rid_b}/cert/{world['b']['cert']}").status_code == 404


@pytest.mark.parametrize("key", ["vendor_a", "client_user_a"])
def test_vendors_and_client_users_cannot_reach_firm_pages(world, key):
    c = as_user(world, key)
    eid = world["a"]["eid"]
    for url in (f"/e/{eid}/portal", f"/e/{eid}/portal/try"):
        assert c.get(url).status_code in (403, 404), url
    r = c.post(f"/e/{eid}/portal/add/category", data={"name": "Sneaky"})
    assert r.status_code in (403, 404)
    assert all(x["name"] != "Sneaky" for x in portal.listing("category", eid))
    if key == "vendor_a":
        assert c.get(f"/e/{eid}").status_code in (403, 404)


def test_client_admin_cannot_manage_portal(world):
    c = as_user(world, "client_admin_a")
    eid = world["a"]["eid"]
    assert c.get(f"/e/{eid}/portal").status_code == 403
    assert c.post(f"/e/{eid}/portal/delete/rule/1").status_code == 403


def test_staff_manage_only_assigned_portals(world):
    a, b = world["a"], world["b"]
    staff = as_user(world, "staff_a")
    assert staff.get(f"/e/{a['eid']}/portal").status_code == 200
    assert staff.get(f"/e/{b['eid']}/portal").status_code == 404
    assert staff.post(f"/e/{b['eid']}/portal/add/category", data={"name": "X"}).status_code == 404
    assert staff.post(f"/e/{b['eid']}/portal/delete/certificate/{b['cert']}").status_code == 404
    assert submit(staff, b, [("CNC", b["equip"])], eid=b["eid"]).status_code == 404
    # Removing another client's row through this client's URL finds nothing.
    assert staff.post(f"/e/{a['eid']}/portal/delete/certificate/{b['cert']}").status_code == 404
    assert portal.get("certificate", b["eid"], b["cert"])
    assert as_user(world, "staff_none").get(f"/e/{a['eid']}/portal").status_code == 404


def test_firm_can_preview_and_see_vendor_requests(world):
    a = world["a"]
    rid = request_id(submit(as_user(world, "vendor_a"), a, [("CNC", a["equip"])]))
    staff = as_user(world, "staff_a")
    page = staff.get(f"/e/{a['eid']}/portal").text
    assert f"/portal/r/{rid}" in page and "vendor1@supply.test" in page
    assert staff.get(f"/portal/r/{rid}").status_code == 200
    r = submit(staff, a, [("CNC", a["equip"])], eid=a["eid"])
    assert r.status_code == 200 and "Requested by staff.a@firm.com" in r.text
    assert as_user(world, "staff_none").get(f"/portal/r/{rid}").status_code == 404


def test_firm_setup_add_and_remove(world):
    eid = world["a"]["eid"]
    admin = as_user(world, "admin")
    admin.post(f"/e/{eid}/portal/add/category", data={"name": "Software", "examples": "SaaS"})
    cat = [c for c in portal.listing("category", eid) if c["name"] == "Software"][0]
    admin.post(f"/e/{eid}/portal/add/rule", data={"category_id": cat["id"], "state": "Any", "project_type": "*",
                                                   "outcome": "review", "note": "Ask us"})
    assert any(r["category_id"] == cat["id"] and r["state"] == "*" for r in portal.listing("rule", eid))
    # An exempt rule needs a certificate type.
    admin.post(f"/e/{eid}/portal/add/rule", data={"category_id": cat["id"], "state": "TX", "project_type": "*",
                                                   "outcome": "exempt", "cert_type": ""})
    assert not any(r["state"] == "TX" and r["category_id"] == cat["id"] for r in portal.listing("rule", eid))
    # Certificates must be PDFs.
    ent = portal.listing("entity", eid)[0]["id"]
    before = len(portal.listing("certificate", eid))
    admin.post(f"/e/{eid}/portal/add/certificate", data={"entity_id": ent, "state": "CA", "cert_type": "resale"},
               files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert len(portal.listing("certificate", eid)) == before
    admin.post(f"/e/{eid}/portal/add/certificate", data={"entity_id": ent, "state": "CA", "cert_type": "resale"},
               files={"file": ("ca.pdf", PDF, "application/pdf")})
    assert len(portal.listing("certificate", eid)) == before + 1
    # Removing a category removes its rules.
    admin.post(f"/e/{eid}/portal/delete/category/{cat['id']}")
    assert not any(r["category_id"] == cat["id"] for r in portal.listing("rule", eid))


def test_deleting_client_removes_portal(world):
    a = world["a"]
    submit(as_user(world, "vendor_a"), a, [("CNC", a["equip"])])
    db.delete_engagement(a["eid"])
    for kind in portal.TABLES:
        assert portal.listing(kind, a["eid"]) == []
    assert portal.requests(a["eid"]) == []
    assert portal.listing("rule", world["b"]["eid"])


def test_setup_explains_rejected_input(world):
    eid = world["a"]["eid"]
    admin = as_user(world, "admin")
    ent = portal.listing("entity", eid)[0]["id"]
    r = admin.post(f"/e/{eid}/portal/add/certificate", data={"entity_id": ent, "state": "CA", "cert_type": "resale"},
                   files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert "not a PDF" in r.text
    r = admin.post(f"/e/{eid}/portal/add/rule", data={"category_id": "0", "outcome": "exempt"})
    assert "exempt rule needs a certificate type" in r.text
