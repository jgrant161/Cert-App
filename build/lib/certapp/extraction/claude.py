"""Read a certificate PDF with Claude and return a validated ``Extraction``."""

from __future__ import annotations

import base64
import json

import anthropic

from .schema import Extraction, json_schema

MODEL = "claude-opus-5-5"

SYSTEM_PROMPT = """\
You are a sales-and-use-tax analyst reading exemption and resale certificates \
that a seller's customers have provided. Your reading feeds a certificate \
schedule a CPA firm hands to its client, so you report exactly what is written \
on the page and never fill gaps with assumptions.

How to read the forms:
- Multistate forms (MTC Uniform Sales & Use Tax Certificate, Streamlined Sales \
Tax Certificate of Exemption, and similar grids) list many states. Report every \
state line printed on the form, including blank ones, with the text written on \
that line. A state only "applies" when a registration number sits on its line, \
but you do not make that call; you classify each line.
- Classify text that is not a number faithfully: "N/A", dashes, "See attached", \
"Multiple", and phrases such as "Wayfair Ruling" or "Exempt per nexus rules" \
are not registration numbers.
- When the same number repeats on many lines, or the form says the home-state \
number or FEIN is being used, classify those lines as home_state_number or fein \
and name the issuing state when you can tell.
- When a line contains a number together with words saying the buyer is not \
registered or is exempt under nexus rules, set disclaims_registration.
- Entries written outside the printed grid (a state added in the margin or \
below the table) count as lines; mention them in observations.
- Filenames are hints from the client and are sometimes wrong. If the document \
is a different state's form, a single-state document labelled multijurisdiction, \
or bundles additional certificates on later pages, say so in observations.
- Entity-based exemptions (federal, state, or local government; federal \
instrumentality; nonprofit; school) carry no resale registration; set \
certificate_type accordingly and record the basis cited.
- Record the form number (e.g. CDTFA-230, ST-5, E-595E, 01-339) and the form's \
printed title separately. Many state forms print the number in a corner or footer; \
multistate forms such as the MTC Uniform certificate often have no number.
- Read every page. Use null for anything not on the document. Dates as YYYY-MM-DD.
"""


class ExtractionError(RuntimeError):
    pass


class ClaudeExtractor:
    name = "claude"

    def __init__(self, client: anthropic.Anthropic | None = None, model: str = MODEL,
                 effort: str = "high"):
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.effort = effort
        self._schema = json_schema()

    def extract(self, pdf_bytes: bytes, filename: str, filename_hint: dict) -> Extraction:
        hint = json.dumps({k: v for k, v in filename_hint.items() if v}, sort_keys=True)
        with self.client.beta.messages.stream(
            model=self.model,
            max_tokens=32000,
            system=SYSTEM_PROMPT,
            thinking={"type": "adaptive"},
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": self._schema},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "document",
                        "source": {
                            "type": "base64",
                            "media_type": "application/pdf",
                            "data": base64.standard_b64encode(pdf_bytes).decode("ascii"),
                        },
                    },
                    {
                        "type": "text",
                        "text": f"File name: {filename}\nFilename hint (may be wrong): {hint}\n\n"
                                "Read this certificate and return the extraction.",
                    },
                ],
            }],
        ) as stream:
            message = stream.get_final_message()

        if message.stop_reason == "refusal":
            raise ExtractionError("The model declined to read this document.")
        if message.stop_reason == "max_tokens":
            raise ExtractionError("Extraction was cut off before it finished (max_tokens).")

        text = "".join(b.text for b in message.content if b.type == "text")
        try:
            return Extraction.model_validate_json(text)
        except ValueError as exc:
            raise ExtractionError(f"Extraction did not match the schema: {exc}") from exc
