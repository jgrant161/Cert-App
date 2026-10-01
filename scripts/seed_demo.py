"""Create a sample engagement for demos and sales calls.

Builds placeholder PDFs and canned readings that mirror the situations a real
review turns up (multistate grids, nexus disclaimers, home-state numbers,
wrong-state filenames, TBD files, duplicates, expired certificates), so the
app can be shown without client data or an API key.

    python scripts/seed_demo.py && python -m certapp serve
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pypdf import PdfWriter  # noqa: E402

from certapp import db  # noqa: E402
from certapp.extraction.schema import Extraction, JurisdictionLine, Party  # noqa: E402
from certapp.ingest import ingest  # noqa: E402
from certapp.pipeline import process_engagement  # noqa: E402


def pdf(tag: str) -> bytes:
    w = PdfWriter()
    w.add_blank_page(612, 792)
    w.add_metadata({"/Title": tag})
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def L(state, raw="", cls="registration_number", issuing=None, disclaims=False, id_value=None):
    if id_value is None and cls in ("registration_number", "home_state_number", "fein"):
        id_value = raw
    return JurisdictionLine(state=state, raw_text=raw, id_value=id_value, classification=cls,
                            id_issuing_state=issuing, disclaims_registration=disclaims, note=None)


MTC = (None, "Uniform Sales & Use Tax Exemption/Resale Certificate - Multijurisdiction")
SST = ("SST", "Streamlined Sales Tax Certificate of Exemption")


def X(lines, buyer, *, multi=False, form=("CDTFA-230", "California Resale Certificate"), family=None,
      ctype="resale", expires=None, reason=None, obs=(), conf="high", signed=True):
    return Extraction(
        form_number=form[0],
        form_name=form[1],
        form_family=family or ("mtc_uniform" if multi else "state_resale"),
        issuing_state=None if multi else lines[0].state,
        purchaser=Party(name=buyer, address=None, state=None),
        seller=Party(name="Demo Seller, LLC", address="Las Vegas, NV", state="NV"),
        certificate_type=ctype, exemption_reason=reason, is_multistate=multi,
        jurisdiction_lines=lines, signed=signed, signature_date="2024-03-01", expiration_date=expires,
        blanket=True, page_count_reviewed=1, observations=list(obs), confidence=conf)


NEXUS = [L(s, "SR-FHA-100-555 & Exempt Per Nexus Rules", "home_state_number", "CA", True, "SR-FHA-100-555")
         for s in ("AZ", "CO", "FL", "GA", "IL", "NJ", "NY", "TX", "WA")]

DEMO = {
    "Acme Supply - Resale Certificate (CA).pdf": X([L("CA", "SR-EA-102-334455")], "Acme Supply Inc."),
    "Alfresco Heating - Exemption Certificate (Multijurisdiction).pdf": X(
        [L("CA", "SR-FHA-100-555")] + NEXUS, "Alfresco Heating", multi=True, form=MTC),
    "Brandster - Resale Certificate (Multijurisdiction).pdf": X(
        [L("FL", "78-8012345678-9"), L("AL", "78-8012345678-9", "home_state_number", "FL"),
         L("CT", "78-8012345678-9", "home_state_number", "FL"), L("TX", "32012345678"),
         L("GA", "", "blank"), L("NV", "N/A", "na")], "Brandster LLC", multi=True, form=MTC),
    "Modern Living Spaces - Resale Certificate (Multijurisdiction).pdf": X(
        [L("CO", "34567890-0001")] + [L(s, "Wayfair Ruling", "non_numeric_text")
                                       for s in ("AZ", "GA", "IL", "TX")],
        "Modern Living Spaces", multi=True, form=MTC),
    "Patio Comforts - Resale Certificate (Multijurisdiction).pdf": X(
        [L(s, "", "blank") for s in ("AR", "GA", "IN", "KS")], "Patio Comforts", multi=True,
        form=SST, family="sst_streamlined",
        reason="Do not meet nexus threshold"),
    "BE Aerospace (Collins Aerospace) - Resale Certificate (FL).pdf": X(
        [L("NC", "600123456")], "BE Aerospace Inc.", form=("E-595E", "Streamlined Sales and Use Tax Certificate of Exemption (North Carolina)"),
        obs=["Filename says FL but this is North Carolina Form E-595E (purchaser in Winston-Salem, NC)."]),
    "Field Aerospace - Resale Certificate (TBD).pdf": X([L("OK", "2001234-56")], "Field Aerospace",
                                                       form=(None, "Oklahoma Sales Tax Permit")),
    "Woods Hole Oceanographic - Exemption Certificate (MA).pdf": X(
        [L("MA", "E-042103580")], "Woods Hole Oceanographic Institution", form=("ST-5", "Sales Tax Exempt Purchaser Certificate (Massachusetts)"),
        ctype="nonprofit", reason="501(c)(3) exempt purchaser", expires="2021-12-31"),
    "Navy Golf Course - Exemption Certificate (CA).pdf": X(
        [L("CA", "", "blank")], "Navy Golf Course", form=(None, "Federal Instrumentality Exemption Letter"),
        family="entity_exemption", ctype="government",
        reason="Federal instrumentality, Cal. Code Regs. tit. 18 §1614"),
    "Renovation Brands - Resale Certificate (Multiple).pdf": X(
        [L("MA", "ST4-0099887")], "Renovation Brands", form=("ST-4", "Sales Tax Resale Certificate (Massachusetts)"), multi=False,
        obs=["Page 1 is a Massachusetts ST-4; pages 2-8 bundle further state certificates that need a page-by-page read."],
        conf="medium"),
    "Yaskawa America - Resale Certificate (Multijurisdiction).pdf": X(
        [L("IL", "1234-5678"), L("OH", "98765432"), L("WI", "456-1234567890-03"), L("AL", "N/A", "na"),
         L("AR", "N/A", "na")], "Yaskawa America Inc.", multi=True, form=MTC),
}
DUPLICATE = ("Yaskawa America - Resale Certificate (Multijurisdiction).pdf",
             "Yaskawa America Inc - Resale Certificate (Multijurisdiction).pdf")
LATER = {
    "Delta Tools - Resale Certificate (TX).pdf": X([L("TX", "32098765432")], "Delta Tools Co.",
                                                   form=("01-339", "Texas Sales and Use Tax Resale Certificate"), expires="2026-12-01"),
    "Sun World - Resale Certificate (Multijurisdiction).pdf": X(
        [L(s, "95-1234567", "fein") for s in ("CA", "GA", "IN", "KY", "MI", "NC")], "Sun World",
        multi=True, form=SST, family="sst_streamlined",
        reason="Do not meet nexus threshold"),
}


class Canned:
    name = "demo-seed"

    def extract(self, pdf_bytes, filename, hint):
        return {**DEMO, **LATER}[filename]


def main() -> None:
    db.init()
    eid = db.create_engagement("Sample Client (demo data)", "Demo Seller, LLC")
    first = [(n, pdf(n)) for n in DEMO] + [(DUPLICATE[1], pdf(DUPLICATE[0]))]
    ingest(eid, first, "Initial certificates")
    process_engagement(eid, Canned())
    ingest(eid, [(n, pdf(n)) for n in LATER], "9.30.26 Certs")
    process_engagement(eid, Canned())
    print(f"Created engagement {eid}. Run: python -m certapp serve  then open http://127.0.0.1:8000/e/{eid}")


if __name__ == "__main__":
    main()
