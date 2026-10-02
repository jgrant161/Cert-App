"""What the extractor returns for one certificate.

The extractor reports *what is on the page*; it does not decide which states
go on the schedule. That decision belongs to the engagement's review policy
(see ``certapp.rules``), so a policy change re-scores every certificate
instantly instead of re-reading the PDFs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FormFamily = Literal[
    "mtc_uniform",          # Multistate Tax Commission Uniform Sales & Use Tax Certificate
    "sst_streamlined",      # Streamlined Sales Tax Certificate of Exemption
    "state_resale",         # a single state's resale / exemption form (CDTFA-230, ST-5, E-595E ...)
    "sellers_permit",       # a permit or registration printout rather than a certificate
    "entity_exemption",     # government / nonprofit / federal-instrumentality exemption
    "other",
]

CertificateType = Literal[
    "resale", "manufacturing", "government", "nonprofit", "agricultural",
    "direct_pay", "entity_based", "other", "unknown",
]

LineClass = Literal[
    "registration_number",  # a state-issued registration / permit number for that state
    "home_state_number",    # another state's number, entered as the home-state number
    "fein",                 # a federal EIN entered in place of a state number
    "na",                   # "N/A", "NA", "None"
    "blank",                # line printed on the form but left empty
    "dash",                 # struck through or dashed out
    "see_attached",         # "See attached"
    "multiple",             # "Multiple", "Various"
    "non_numeric_text",     # any other words (e.g. "Wayfair Ruling", "Exempt per nexus rules")
]


class JurisdictionLine(BaseModel):
    state: str = Field(description="Two-letter USPS code of the state this line belongs to")
    raw_text: str = Field(description="Exactly what is written on the line, '' if blank")
    id_value: str | None = Field(description="The ID number on the line, if any, without surrounding words")
    classification: LineClass
    id_issuing_state: str | None = Field(
        description="Two-letter code of the state that issued id_value when it is not this line's state"
    )
    disclaims_registration: bool = Field(
        description="True when the line's text says the buyer is NOT registered there "
                    "(e.g. '& Exempt Per Nexus Rules', 'not registered', 'no nexus')"
    )
    note: str | None


class Party(BaseModel):
    name: str | None
    address: str | None
    state: str | None


class Extraction(BaseModel):
    form_number: str | None = Field(
        default=None,  # absent on readings saved before this field existed
        description="The form's number or identifier exactly as printed, e.g. 'CDTFA-230', 'ST-5', "
                    "'E-595E', '01-339', 'ST-120'. Null when the form carries no number")
    form_name: str | None = Field(
        description="The form's printed title, e.g. 'California Resale Certificate', "
                    "'Uniform Sales & Use Tax Exemption/Resale Certificate - Multijurisdiction', "
                    "'Streamlined Sales Tax Certificate of Exemption'")
    form_family: FormFamily
    issuing_state: str | None = Field(description="State whose form this is, null for multistate forms")
    purchaser: Party
    seller: Party
    certificate_type: CertificateType
    exemption_reason: str | None
    is_multistate: bool
    jurisdiction_lines: list[JurisdictionLine] = Field(
        description="For multistate forms: every state line printed on the form, including blank ones. "
                    "For single-state forms: one line for that state."
    )
    signed: bool | None
    signature_date: str | None = Field(description="ISO date YYYY-MM-DD if legible")
    expiration_date: str | None = Field(description="ISO date YYYY-MM-DD, null if none stated")
    blanket: bool | None
    page_count_reviewed: int
    observations: list[str] = Field(
        description="Anything a reviewer should know: wrong form for the state, extra bundled "
                    "certificates on later pages, illegible areas, entries written outside the grid"
    )
    confidence: Literal["high", "medium", "low"]


def json_schema() -> dict:
    """Strict JSON schema for structured outputs (every key required, no extras)."""
    schema = Extraction.model_json_schema()
    _strictify(schema)
    for d in schema.get("$defs", {}).values():
        _strictify(d)
    return schema


def _strictify(node: dict) -> None:
    if node.get("type") == "object" and "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node["properties"].keys())
        for prop in node["properties"].values():
            prop.pop("default", None)
            prop.pop("title", None)
            _strictify(prop)
    node.pop("title", None)
    for key in ("items",):
        if isinstance(node.get(key), dict):
            _strictify(node[key])
    for key in ("anyOf",):
        for sub in node.get(key, []):
            _strictify(sub)
