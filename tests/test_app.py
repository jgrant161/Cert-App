"""End to end through the web app: upload a zip, read, review, add a batch, export."""

import io
import zipfile

from fastapi.testclient import TestClient
from openpyxl import load_workbook

from certapp.extraction.schema import json_schema
from certapp.web.app import create_app

from conftest import FakeExtractor, extraction, line, make_pdf

FILES = {
    "Acme Supply - Resale Certificate (CA).pdf": extraction([line("CA", "SR-111")], purchaser="Acme"),
    "Brandster - Resale Certificate (Multijurisdiction).pdf": extraction(
        [line("FL", "FL-7"), line("AL", "FL-7", "home_state_number", issuing="FL"),
         line("GA", "", "blank")], multistate=True, purchaser="Brandster"),
    "Collins - Resale Certificate (FL).pdf": extraction([line("NC", "600123")], purchaser="Collins"),
}
LATER = {"Delta Tools - Resale Certificate (TX).pdf": extraction([line("TX", "3200")], purchaser="Delta")}


def zip_of(names):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for n in names:
            zf.writestr(f"Certificates/{n}", make_pdf(n))
        zf.writestr("__MACOSX/._junk", b"x")
    return buf.getvalue()


def test_full_engagement_flow():
    fake = FakeExtractor({**FILES, **LATER})
    answers = []
    client = TestClient(create_app(extractor=fake, ask_fn=lambda q, ctx, h: answers.append(ctx) or "answer"))

    r = client.post("/engagements", data={"client_name": "Additive Manufacturing, LLC"}, follow_redirects=False)
    eid = int(r.headers["location"].rsplit("/", 1)[1])

    r = client.post(f"/e/{eid}/upload", data={"label": "Initial"},
                    files=[("files", ("Certificates.zip", zip_of(FILES), "application/zip"))])
    assert r.status_code == 200
    assert sorted(fake.calls) == sorted(FILES)

    page = client.get(f"/e/{eid}").text
    assert "SR-111" in page and "AL" in page

    # Second batch: one new file, one re-sent file (skipped, not re-read).
    resend = zip_of(["Acme Supply - Resale Certificate (CA).pdf", *LATER])
    client.post(f"/e/{eid}/upload", data={"label": "9.30.26 Certs"},
                files=[("files", ("9.30.26 Certs.zip", resend, "application/zip"))])
    assert fake.calls.count("Acme Supply - Resale Certificate (CA).pdf") == 1
    assert "Delta Tools - Resale Certificate (TX).pdf" in fake.calls
    batches = client.get(f"/e/{eid}?tab=batches").text
    assert "9.30.26 Certs" in batches

    # Reviewer excludes Brandster's AL line.
    cert_page = client.get(f"/e/{eid}/c/2")
    assert cert_page.status_code == 200 and "Home-state number" in cert_page.text
    client.post(f"/e/{eid}/c/2/override", data={"state": "AL", "decision": "exclude", "note": "per client"})
    client.post(f"/e/{eid}/c/2/review", data={"review_status": "reviewed", "note": "checked"})

    # Policy page renders and saves.
    assert client.get(f"/e/{eid}?tab=policy").status_code == 200
    client.post(f"/e/{eid}/policy", data={"include_fein": "on", "expiring_soon_days": "60"})

    # Follow-up tab shows the wrong-state filename.
    assert "filename state mismatch" in client.get(f"/e/{eid}?tab=followup").text

    # Excel deliverable
    r = client.get(f"/e/{eid}/export.xlsx")
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames[0] == "Overview"
    sched = [row for row in wb["Schedule"].iter_rows(min_row=2, values_only=True)]
    assert sorted((row[0], row[2]) for row in sched) == [
        ("Acme Supply", "CA"), ("Brandster", "FL"), ("Collins", "NC"), ("Delta Tools", "TX")]
    recon = list(wb["Reconciliation"].iter_rows(min_row=2, values_only=True))
    assert recon[-1][0] == "TOTAL (4 files)" and recon[-1][3] == 4
    assert any(row[0] == "Brandster" and row[1] == "AL" for row in
               wb["Excluded Lines"].iter_rows(min_row=2, values_only=True))

    # Q&A gets the engagement data as context (demo mode answers without calling out).
    client.post(f"/e/{eid}/ask", data={"question": "Which files were renamed?"})
    assert "Which files were renamed?" in client.get(f"/e/{eid}?tab=ask").text


def test_extraction_errors_are_isolated_and_retryable():
    fake = FakeExtractor({})
    client = TestClient(create_app(extractor=fake))
    client.post("/engagements", data={"client_name": "X"})
    client.post("/e/1/upload", files=[("files", ("Bad - Resale Certificate (CA).pdf", make_pdf("bad"), "application/pdf"))])
    assert "unreadable" in client.get("/e/1/c/1").text
    fake.by_filename["Bad - Resale Certificate (CA).pdf"] = extraction([line("CA", "SR-1")])
    client.post("/e/1/c/1/retry")
    assert "SR-1" in client.get("/e/1").text


def test_json_schema_is_strict():
    schema = json_schema()

    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "object" and "properties" in node:
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(schema)
