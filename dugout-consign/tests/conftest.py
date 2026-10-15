import os

import pytest
from fastapi.testclient import TestClient

from consign import auth, db

TEST_PG = os.environ.get("CONSIGN_TEST_DATABASE_URL")
PASSWORD = "correct horse battery"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSIGN_DATA_DIR", str(tmp_path / "data"))
    for var in ("CONSIGN_ENV", "CONSIGN_SECRET_KEY", "CONSIGN_FILES_DIR", "CONSIGN_BASE_URL", "DATABASE_URL",
                "SQUARE_ACCESS_TOKEN", "SQUARE_LOCATION_ID", "SQUARE_ENVIRONMENT",
                "SQUARE_WEBHOOK_SIGNATURE_KEY", "SQUARE_WEBHOOK_URL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setitem(auth._SCRYPT, "n", 2 ** 10)   # fast hashing in tests only
    if TEST_PG:
        monkeypatch.setenv("DATABASE_URL", TEST_PG)
        import psycopg
        with psycopg.connect(TEST_PG, autocommit=True) as conn:
            conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    db.init()
    return tmp_path


def make_user(role: str, email: str | None = None, consignor_id: int | None = None) -> dict:
    uid = db.create_user(email or f"{role}@example.com", role.title(), role, auth.hash_password(PASSWORD),
                         consignor_id=consignor_id)
    return db.get_user(uid)


def sign_in(client, user: dict):
    r = client.post("/login", data={"email": user["email"], "password": PASSWORD})
    assert r.status_code == 200, r.text[:500]
    return client


@pytest.fixture
def owner():
    return make_user("owner")


@pytest.fixture
def make_client():
    from consign.web.app import create_app

    def make(square_factory=None):
        return TestClient(create_app(square_factory=square_factory))
    return make


def add_item(cid: int, title="Card", price=10000, status="listed", **kw) -> dict:
    iid = db.create_item(consignor_id=cid, title=title, status=status, price_cents=price, **kw)
    return db.get_item(iid)
