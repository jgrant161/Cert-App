"""Offline extractor for demos and trials without an API key.

It trusts the filename and pulls a candidate ID from the PDF's text layer.
It cannot read scanned grids, so multistate files come back with no lines and
a low-confidence flag telling the reviewer the file still needs a real read.
"""

from __future__ import annotations

import io
import re

from pypdf import PdfReader

from .schema import Extraction, JurisdictionLine, Party

_ID_RE = re.compile(r"\b(?=[A-Z0-9\-]*\d)[A-Z0-9]{2,4}[\- ]?\d{3,}[\-\d]*\b")


def _pdf_text(pdf_bytes: bytes) -> tuple[str, int]:
    try:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        return "\n".join(p.extract_text() or "" for p in reader.pages), len(reader.pages)
    except Exception:  # unreadable or encrypted PDFs still get a row
        return "", 0


class DemoExtractor:
    name = "demo"

    def extract(self, pdf_bytes: bytes, filename: str, filename_hint: dict) -> Extraction:
        text, pages = _pdf_text(pdf_bytes)
        state = filename_hint.get("state")
        lines: list[JurisdictionLine] = []
        obs = ["Demo mode: certificate was not read by AI; values come from the filename."]
        if state:
            m = _ID_RE.search(text.upper())
            lines.append(JurisdictionLine(
                state=state, raw_text=m.group(0) if m else "",
                id_value=m.group(0) if m else None,
                classification="registration_number",
                id_issuing_state=None, disclaims_registration=False,
                note=None if m else "ID not found in text layer",
            ))
        else:
            obs.append("Multistate or undetermined file: needs an AI or manual read of the grid.")
        return Extraction(
            form_name=None,
            form_family="other" if state else "mtc_uniform",
            issuing_state=state,
            purchaser=Party(name=filename_hint.get("company"), address=None, state=None),
            seller=Party(name=None, address=None, state=None),
            certificate_type="resale" if "resale" in (filename_hint.get("cert_type") or "").lower() else "unknown",
            exemption_reason=None,
            is_multistate=not state,
            jurisdiction_lines=lines,
            signed=None, signature_date=None, expiration_date=None, blanket=None,
            page_count_reviewed=pages,
            observations=obs,
            confidence="low",
        )
