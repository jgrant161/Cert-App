"""Sign-in, roles, and client separation.

The separation tests walk every client-data route as every role, so a new
route that forgets its permission check fails here.
"""

import re

import pytest
from fastapi.testclient import TestClient

from certapp import auth, db
from certapp.ingest import ingest
from certapp.pipeline import process_engagement
from certapp.web.app import create_app

from conftest import TEST_PASSWORD, FakeExtractor, extraction, line, make_pdf, make_user, sign_in

CERT = "Acme Supply - Resale Certificate (CA).pdf"


def new_client(**kw):
    return TestClient(create_app(extractor=FakeExtractor({CERT: extraction([line("CA", "SR-1")])}), **kw))


@pytest.fixture
def world():
    """Two clients (A and B), each with one read certificate, and a user of every kind."""
    a = db.create_engagement("Client A")
    b = db.create_engagement("Client B")
    fake = FakeExtractor({CERT: extraction([line("CA", "SR-1")])})
    for eid in (a, b):
        ingest(eid, [(CERT, make_pdf(f"e{eid}"))], "First")
        process_engagement(eid, fake, workers=1)
    users = {
        "admin": make_user("firm_admin", email="admin@firm.com"),
        "staff_a": make_user("firm_staff", email="staff.a@firm.com"),
        "staff_none": make_user("firm_staff", email="staff.none@firm.com"),
        "client_admin_a": make_user("client_admin", a, email="boss@a.com"),
        "client_user_a": make_user("client_user", a, email="clerk@a.com"),
        "client_user_b": make_user("client_user", b, email="clerk@b.com"),
    }
    db.assign_staff(a, users["staff_a"]["id"])
    cert = {eid: db.list_certificates(eid)[0]["id"] for eid in (a, b)}
    return {"a": a, "b": b, "users": users, "cert": cert}


def as_user(world, key):
    return sign_in(new_client(), world["users"][key])


def routes(eid, cid):
    """Every route that reads or changes one client's data: (method, path, form data, permission)."""
    return [
        ("GET", f"/e/{eid}", None, "view"),
        ("GET", f"/e/{eid}?tab=followup", None, "view"),
        ("GET", f"/e/{eid}/c/{cid}", None, "view"),
        ("GET", f"/e/{eid}/c/{cid}/pdf", None, "view"),
        ("GET", f"/e/{eid}/export.xlsx", None, "export"),
        ("POST", f"/e/{eid}/process", {}, "work"),
        ("POST", f"/e/{eid}/c/{cid}/override", {"state": "CA", "decision": "exclude"}, "work"),
        ("POST", f"/e/{eid}/c/{cid}/review", {"review_status": "reviewed"}, "work"),
        ("POST", f"/e/{eid}/c/{cid}/retry", {}, "work"),
        ("POST", f"/e/{eid}/policy", {"include_na": "on"}, "policy"),
        ("POST", f"/e/{eid}/ask", {"question": "hi"}, "ask"),
        ("POST", f"/e/{eid}/delete", {"confirm_name": "wrong name"}, "delete"),
    ]


def call(client, method, path, data):
    if method == "GET":
        return client.get(path, follow_redirects=False)
    return client.post(path, data=data, follow_redirects=False)


# --- signing in ------------------------------------------------------------

def test_first_run_setup_creates_admin_then_closes():
    client = new_client()
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == "/setup"
    r = client.post("/setup", data={"name": "Pat Lee", "email": "Pat@Firm.com", "method": "password",
                                    "password": "short", "confirm": "short"})
    assert r.status_code == 400 and "at least 12" in r.text
    r = client.post("/setup", data={"name": "Pat Lee", "email": "Pat@Firm.com", "method": "password",
                                    "password": TEST_PASSWORD, "confirm": TEST_PASSWORD})
    assert "Client engagements" in r.text and "Firm administrator" in r.text
    assert db.get_user_by_email("pat@firm.com")["role"] == "firm_admin"
    assert new_client().get("/setup", follow_redirects=False).headers["location"] == "/login"


def test_production_setup_needs_the_setup_code(monkeypatch):
    monkeypatch.setenv("CERTAPP_ENV", "production")
    monkeypatch.setenv("CERTAPP_SECRET_KEY", "x" * 40)
    monkeypatch.setenv("CERTAPP_SETUP_TOKEN", "open-sesame-123")
    client = new_client()
    form = {"name": "Pat", "email": "pat@firm.com", "method": "password",
            "password": TEST_PASSWORD, "confirm": TEST_PASSWORD}
    assert client.post("/setup", data={**form, "setup_token": "guess"}).status_code == 400
    assert db.count_users() == 0
    client.post("/setup", data={**form, "setup_token": "open-sesame-123"})
    assert db.count_users() == 1


def test_production_requires_a_secret_key(monkeypatch):
    monkeypatch.setenv("CERTAPP_ENV", "production")
    with pytest.raises(RuntimeError, match="CERTAPP_SECRET_KEY"):
        create_app()


def test_pages_require_sign_in(world):
    client = new_client()
    for path in ("/", f"/e/{world['a']}", "/users", "/activity", f"/e/{world['a']}/export.xlsx"):
        r = client.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/login?next="), path
    assert client.post(f"/e/{world['a']}/delete", data={"confirm_name": "Client A"},
                       follow_redirects=False).headers["location"].startswith("/login")
    assert db.get_engagement(world["a"])


def test_wrong_password_and_lockout(world):
    client = new_client()
    email = world["users"]["client_user_a"]["email"]
    for _ in range(5):
        r = client.post("/login", data={"email": email, "password": "nope nope nope"})
        assert "do not match" in r.text
    r = client.post("/login", data={"email": email, "password": TEST_PASSWORD})
    assert "Too many attempts" in r.text
    r = client.post("/login", data={"email": "nobody@x.com", "password": "nope nope nope"})
    assert "do not match" in r.text            # same message whether or not the account exists


def test_sign_out(world):
    client = as_user(world, "admin")
    client.post("/logout")
    assert client.get("/", follow_redirects=False).status_code == 303


@pytest.mark.parametrize("target", ["//evil.example.com/", "/\\evil.example.com", "https://evil.example.com"])
def test_next_parameter_cannot_redirect_off_site(world, target):
    client = new_client()
    r = client.post("/login", data={"email": "admin@firm.com", "password": TEST_PASSWORD,
                                    "next": target}, follow_redirects=False)
    assert r.headers["location"] == "/"


# --- client separation -----------------------------------------------------

def test_client_and_staff_cannot_reach_other_clients(world):
    b, cid_b = world["b"], world["cert"][world["b"]]
    for key in ("staff_a", "staff_none", "client_admin_a", "client_user_a"):
        client = as_user(world, key)
        for method, path, data, _ in routes(b, cid_b):
            r = call(client, method, path, data)
            assert r.status_code == 404, (key, method, path, r.status_code)
    assert db.get_engagement(b) and db.list_certificates(b)[0]["review_status"] == "open"


def test_certificate_from_another_client_cannot_be_reached_through_own_client(world):
    a, cid_b = world["a"], world["cert"][world["b"]]
    client = as_user(world, "client_user_a")
    for path in (f"/e/{a}/c/{cid_b}", f"/e/{a}/c/{cid_b}/pdf"):
        assert client.get(path).status_code == 404


@pytest.mark.parametrize("key", ["admin", "staff_a", "client_admin_a", "client_user_a"])
def test_role_permissions_on_own_client(world, key):
    a, cid = world["a"], world["cert"][world["a"]]
    user = world["users"][key]
    client = as_user(world, key)
    for method, path, data, permission in routes(a, cid):
        r = call(client, method, path, data)
        if auth.can(user, permission):
            assert r.status_code in (200, 303), (key, path, r.status_code)
        else:
            assert r.status_code == 403, (key, path, r.status_code)
    assert db.get_engagement(a)                # wrong confirmation name never deletes


def test_client_view_is_read_only(world):
    client = as_user(world, "client_user_a")
    r = client.get("/", follow_redirects=False)
    assert r.headers["location"] == f"/e/{world['a']}"          # straight to their company
    page = client.get(f"/e/{world['a']}").text
    assert "SR-1" in page and "Download Excel schedule" in page
    for hidden in ("Upload and read", "Review policy", "Ask about this client", "Delete this client",
                   "Excluded lines", "Assigned staff"):
        assert hidden not in page, hidden
    cert_page = client.get(f"/e/{world['a']}/c/{world['cert'][world['a']]}").text
    assert "Undo override" not in cert_page and ">Exclude<" not in cert_page.replace("\n", "").replace(" ", "")
    assert 'name="review_status"' not in cert_page
    assert client.get(f"/e/{world['a']}?tab=policy").text.count("Save policy") == 0
    assert "reviewer checks" not in page


def test_staff_see_only_assigned_clients(world):
    page = as_user(world, "staff_a").get("/").text
    assert "Client A" in page and "Client B" not in page
    assert "New client" not in page
    page = as_user(world, "staff_none").get("/").text
    assert "No clients are assigned to you yet" in page
    page = as_user(world, "admin").get("/").text
    assert "Client A" in page and "Client B" in page and "New client" in page


def test_only_admins_create_clients(world):
    r = as_user(world, "staff_a").post("/engagements", data={"client_name": "Sneaky"})
    assert r.status_code == 403
    assert not any(e["client_name"] == "Sneaky" for e in db.list_engagements())


def test_activity_log_is_scoped(world):
    admin = as_user(world, "admin")
    admin.get(f"/e/{world['b']}/export.xlsx")
    staff = as_user(world, "staff_a")
    staff.get(f"/e/{world['a']}/export.xlsx")
    page = staff.get("/activity").text
    assert "Client A" in page and "Client B" not in page
    assert "Client B" in admin.get("/activity").text
    assert as_user(world, "client_admin_a").get("/activity").status_code == 403


# --- user management -------------------------------------------------------

def test_admin_adds_staff_with_assignment_and_invite_link(world):
    client = as_user(world, "admin")
    r = client.post("/users/new", data={"name": "New Staff", "email": "new@firm.com", "role": "firm_staff",
                                        "auth_method": "password", "staff_engagements": [str(world["b"])]})
    assert "Send this sign-in link" in r.text
    link = re.search(r'value="(http[^"]+/invite/[^"]+)"', r.text).group(1)
    token_path = link.split("testserver")[1]
    newbie = db.get_user_by_email("new@firm.com")
    assert db.staff_engagement_ids(newbie["id"]) == [world["b"]]

    fresh = new_client()
    assert "Welcome, New Staff" in fresh.get(token_path).text
    r = fresh.post(token_path, data={"password": TEST_PASSWORD, "confirm": TEST_PASSWORD})
    assert "Client B" in r.text and "Client A" not in r.text
    assert "expired or was already used" in new_client().get(token_path).text    # one use only


def test_expired_invite_is_refused(world):
    token = auth.create_invite_token(world["users"]["client_user_a"]["id"])
    with db.connect() as conn:
        conn.execute("UPDATE invite SET expires_at = '2000-01-01T00:00:00+00:00'")
    assert new_client().get(f"/invite/{token}").status_code == 404


def test_client_admin_manages_only_own_company(world):
    client = as_user(world, "client_admin_a")
    page = client.get("/users").text
    assert "clerk@a.com" in page and "clerk@b.com" not in page and "staff.a@firm.com" not in page

    # Even if the form names client B, the new user lands in the admin's own company.
    client.post("/users/new", data={"name": "Helper", "email": "helper@a.com", "role": "client_user",
                                    "engagement_id": str(world["b"])})
    assert db.get_user_by_email("helper@a.com")["engagement_id"] == world["a"]

    r = client.post("/users/new", data={"name": "Bad", "email": "bad@a.com", "role": "firm_admin"})
    assert r.status_code == 400 and not db.get_user_by_email("bad@a.com")

    for key in ("client_user_b", "staff_a", "admin"):
        uid = world["users"][key]["id"]
        assert client.get(f"/users/{uid}").status_code == 404
        assert client.post(f"/users/{uid}", data={"name": "x", "role": "client_user"}).status_code == 404


def test_staff_and_client_users_cannot_manage_users(world):
    for key in ("staff_a", "client_user_a"):
        client = as_user(world, key)
        assert client.get("/users").status_code == 403
        assert client.post("/users/new", data={"name": "x", "email": "x@x.com",
                                               "role": "firm_admin"}).status_code == 403


def test_deactivating_signs_user_out_immediately(world):
    victim = as_user(world, "client_user_a")
    assert victim.get(f"/e/{world['a']}").status_code == 200
    admin = as_user(world, "admin")
    uid = world["users"]["client_user_a"]["id"]
    admin.post(f"/users/{uid}", data={"name": "Clerk", "role": "client_user"})   # active box unticked
    assert victim.get(f"/e/{world['a']}", follow_redirects=False).status_code == 303
    r = new_client().post("/login", data={"email": "clerk@a.com", "password": TEST_PASSWORD})
    assert "do not match" in r.text


def test_admin_cannot_lock_themselves_out(world):
    admin = as_user(world, "admin")
    uid = world["users"]["admin"]["id"]
    r = admin.post(f"/users/{uid}", data={"name": "Admin", "role": "firm_admin"})
    assert r.status_code == 400 and db.get_user(uid)["active"] == 1


def test_password_change_signs_out_other_devices(world):
    laptop = as_user(world, "client_admin_a")
    phone = as_user(world, "client_admin_a")
    new_pw = "a brand new long password"
    r = laptop.post("/account/password", data={"current": TEST_PASSWORD, "password": new_pw, "confirm": new_pw})
    assert "Password changed" in r.text
    assert laptop.get(f"/e/{world['a']}").status_code == 200
    assert phone.get(f"/e/{world['a']}", follow_redirects=False).status_code == 303


def test_deleting_a_client_removes_its_users(world):
    admin = as_user(world, "admin")
    admin.post(f"/e/{world['b']}/delete", data={"confirm_name": "Client B"})
    assert db.get_user_by_email("clerk@b.com") is None
    assert db.get_user_by_email("clerk@a.com") is not None


# --- Microsoft 365 ---------------------------------------------------------

class FakeMicrosoft:
    def __init__(self, email=None, error=None):
        self.email, self.error = email, error

    def start(self, redirect_uri):
        return {"auth_uri": "https://login.microsoftonline.com/fake?redirect=" + redirect_uri, "state": "s"}

    def finish(self, flow, params):
        return self.email, self.error


def test_microsoft_sign_in_for_known_staff(world):
    make_user("firm_staff", email="kim@firm.com", method="microsoft")
    client = new_client(microsoft_login=FakeMicrosoft(email="kim@firm.com"))
    assert "Sign in with Microsoft" in client.get("/login").text
    r = client.get("/auth/microsoft", follow_redirects=False)
    assert r.headers["location"].startswith("https://login.microsoftonline.com/")
    r = client.get("/auth/microsoft/callback?code=abc&state=s")
    assert "Kim" in r.text or "Firm Staff" in r.text
    assert client.get("/account").text.count("Microsoft 365") >= 1


def test_microsoft_sign_in_refuses_unknown_and_password_accounts(world):
    for email in ("stranger@firm.com", "clerk@a.com"):
        client = new_client(microsoft_login=FakeMicrosoft(email=email))
        client.get("/auth/microsoft", follow_redirects=False)
        r = client.get("/auth/microsoft/callback?code=abc&state=s")
        assert "not set up for this app" in r.text
        assert client.get("/", follow_redirects=False).status_code == 303


def test_microsoft_callback_without_starting_is_refused(world):
    client = new_client(microsoft_login=FakeMicrosoft(email="admin@firm.com"))
    r = client.get("/auth/microsoft/callback?code=abc&state=s")
    assert "expired" in r.text


def test_microsoft_users_cannot_use_password_form(world):
    make_user("firm_staff", email="kim@firm.com", method="microsoft")
    r = new_client().post("/login", data={"email": "kim@firm.com", "password": TEST_PASSWORD})
    assert "signs in with Microsoft" in r.text


# --- hardening -------------------------------------------------------------

def test_cross_site_posts_are_refused(world):
    client = as_user(world, "admin")
    r = client.post(f"/e/{world['a']}/delete", data={"confirm_name": "Client A"},
                    headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403 and db.get_engagement(world["a"])


def test_security_headers_and_health_check(world):
    r = new_client().get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert r.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]


def test_session_cookie_is_http_only(world):
    client = new_client()
    r = client.post("/login", data={"email": "admin@firm.com", "password": TEST_PASSWORD}, follow_redirects=False)
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
