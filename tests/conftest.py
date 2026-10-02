import io
import os

import pytest
from pypdf import PdfWriter

from certapp import auth, db
from certapp.extraction.schema import Extraction, JurisdictionLine, Party

# Set CERTAPP_TEST_DATABASE_URL=postgresql://... to run the whole suite against PostgreSQL.
TEST_PG = os.environ.get("CERTAPP_TEST_DATABASE_URL")
TEST_PASSWORD = "correct horse battery"


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CERTAPP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CERTAPP_EXTRACTOR", "demo")
    for var in ("CERTAPP_ENV", "CERTAPP_SECRET_KEY", "CERTAPP_MS_TENANT_ID", "CERTAPP_MS_CLIENT_ID",
                "CERTAPP_MS_CLIENT_SECRET", "CERTAPP_BASE_URL", "CERTAPP_FILES_DIR", "DATABASE_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(auth._SCRYPT, "n", 2 ** 10)      # fast hashing in tests only
    if TEST_PG:
        monkeypatch.setenv("DATABASE_URL", TEST_PG)
        import psycopg
        with psycopg.connect(TEST_PG, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db.init()
    return tmp_path


def make_user(role: str, engagement_id: int | None = None, email: str | None = None,
              method: str = "password") -> dict:
    email = email or f"{role}{engagement_id or ''}@example.com"
    uid = db.create_user(email, role.replace("_", " ").title(), role, method,
                         engagement_id=engagement_id,
                         password_hash=auth.hash_password(TEST_PASSWORD) if method == "password" else None)
    return db.get_user(uid)


def sign_in(client, user: dict):
    r = client.post("/login", data={"email": user["email"], "password": TEST_PASSWORD})
    assert r.status_code == 200 and "Sign in" not in r.text.split("<main>")[1][:200], r.text[:500]
    return client


def admin_client(client):
    """Sign the test client in as a firm administrator."""
    return sign_in(client, make_user("firm_admin", email="admin@example.com"))


def make_pdf(tag: str) -> bytes:
    """A one-page PDF whose bytes differ per tag."""
    w = PdfWriter()
    w.add_blank_page(612, 792)
    w.add_metadata({"/Title": tag})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def line(state, raw="", cls="registration_number", id_value=None, issuing=None, disclaims=False):
    if id_value is None and cls in ("registration_number", "home_state_number", "fein"):
        id_value = raw
    return JurisdictionLine(state=state, raw_text=raw, id_value=id_value, classification=cls,
                            id_issuing_state=issuing, disclaims_registration=disclaims, note=None)


def extraction(lines, *, multistate=False, purchaser="Buyer", form="Form", form_number=None, family=None,
               cert_type="resale", expires=None, signed=True, observations=(), confidence="high"):
    return Extraction(
        form_number=form_number, form_name=form, form_family=family or ("mtc_uniform" if multistate else "state_resale"),
        issuing_state=None if multistate else lines[0].state if lines else None,
        purchaser=Party(name=purchaser, address=None, state=None),
        seller=Party(name="Seller", address=None, state=None),
        certificate_type=cert_type, exemption_reason=None, is_multistate=multistate,
        jurisdiction_lines=list(lines), signed=signed, signature_date=None,
        expiration_date=expires, blanket=True, page_count_reviewed=1,
        observations=list(observations), confidence=confidence,
    )


class FakeExtractor:
    """Returns a canned extraction per filename."""
    name = "fake"

    def __init__(self, by_filename: dict[str, Extraction]):
        self.by_filename = by_filename
        self.calls: list[str] = []

    def extract(self, pdf_bytes, filename, filename_hint):
        self.calls.append(filename)
        if filename not in self.by_filename:
            raise RuntimeError("unreadable")
        return self.by_filename[filename]
