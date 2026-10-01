import io

import pytest
from pypdf import PdfWriter

from certapp import db
from certapp.extraction.schema import Extraction, JurisdictionLine, Party


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("CERTAPP_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CERTAPP_EXTRACTOR", "demo")
    db.init()
    return tmp_path


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


def extraction(lines, *, multistate=False, purchaser="Buyer", form="Form", family=None,
               cert_type="resale", expires=None, signed=True, observations=(), confidence="high"):
    return Extraction(
        form_name=form, form_family=family or ("mtc_uniform" if multistate else "state_resale"),
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
