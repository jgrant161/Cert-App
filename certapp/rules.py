"""Turn extractions into a certificate schedule.

This is where reviewer judgment lives. Each engagement carries a ``Policy``
whose toggles encode the calls a reviewer makes when a line holds something
other than a clean registration number ("N/A", a home-state number, an FEIN,
a number followed by "exempt per nexus rules" ...). Changing a toggle
re-scores every certificate without re-reading a single PDF, and per-line
overrides let the reviewer make one-off exceptions on top.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field, asdict, fields
from datetime import date, timedelta

from .extraction.schema import Extraction, JurisdictionLine


@dataclass
class Policy:
    include_home_state_numbers: bool = True   # home-state number entered on another state's line
    include_fein: bool = True                 # FEIN entered in place of a state number
    include_disclaimed: bool = False          # number + "not registered / exempt per nexus rules"
    include_na: bool = False                  # "N/A"
    include_text_entries: bool = False        # "See attached", "Multiple", "Wayfair Ruling" ...
    schedule_exact_duplicates: bool = False   # byte-identical files under another name
    schedule_content_duplicates: bool = True  # same buyer, form and numbers, different file
    expiring_soon_days: int = 90

    @classmethod
    def from_json(cls, text: str | None) -> "Policy":
        data = json.loads(text or "{}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def as_dict(self) -> dict:
        return asdict(self)


POLICY_LABELS = {
    "include_home_state_numbers": "Schedule lines carrying the home-state number",
    "include_fein": "Schedule lines carrying an FEIN instead of a state number",
    "include_disclaimed": "Schedule lines whose text disclaims registration (e.g. \"exempt per nexus rules\")",
    "include_na": "Schedule lines reading \"N/A\"",
    "include_text_entries": "Schedule lines with text instead of a number (\"See attached\", \"Multiple\", \"Wayfair Ruling\")",
    "schedule_exact_duplicates": "Schedule byte-identical duplicate files",
    "schedule_content_duplicates": "Schedule certificates whose content duplicates another file",
}


@dataclass
class Flag:
    code: str
    severity: str   # follow_up (ask the client) | review (reviewer judgment) | info
    message: str


@dataclass
class ScheduleRow:
    certificate_id: int
    company: str
    certificate_type: str
    state: str
    registration: str | None
    form: str | None
    source_file: str
    batch: str
    basis: str      # single_state | exploded | tbd_resolved | corrected | override
    flags: list[str] = field(default_factory=list)
    note: str | None = None


@dataclass
class ExcludedLine:
    certificate_id: int
    company: str
    state: str
    raw_text: str
    classification: str
    reason: str
    source_file: str


@dataclass
class CertificateResult:
    cert: dict
    extraction: Extraction | None
    rows: list[ScheduleRow]
    excluded: list[ExcludedLine]
    flags: list[Flag]
    line_decisions: list[dict]   # every line with its decision, for the detail view


@dataclass
class EngagementResult:
    certificates: list[CertificateResult]

    @property
    def rows(self) -> list[ScheduleRow]:
        return [r for c in self.certificates for r in c.rows]

    @property
    def excluded(self) -> list[ExcludedLine]:
        return [x for c in self.certificates for x in c.excluded]

    def by_state(self) -> list[tuple[str, int]]:
        return sorted(Counter(r.state for r in self.rows).items())

    def by_company(self) -> list[tuple[str, int, str]]:
        states: dict[str, set] = {}
        for r in self.rows:
            states.setdefault(r.company, set()).add(r.state)
        return sorted(((c, len(s), ", ".join(sorted(s))) for c, s in states.items()),
                      key=lambda t: t[0].lower())

    def flag_counts(self) -> Counter:
        return Counter(f.severity for c in self.certificates for f in c.flags)


# --- line decisions --------------------------------------------------------

# Certificate flags that describe particular lines; rows carry them only when they apply.
_LINE_LEVEL = {"repeated_number", "registration_disclaimed"}

_ALWAYS_OUT = {"blank": "Line left blank", "dash": "Line dashed out"}


def decide_line(line: JurisdictionLine, policy: Policy) -> tuple[bool, str]:
    """Return (include, reason) for one jurisdiction line under the policy."""
    c = line.classification
    if c in _ALWAYS_OUT:
        return False, _ALWAYS_OUT[c]
    if line.disclaims_registration and not policy.include_disclaimed:
        return False, "Text on the line disclaims registration in this state"
    if c == "registration_number":
        return True, "Registration number on the line"
    if c == "home_state_number":
        return policy.include_home_state_numbers, "Home-state number used on this line"
    if c == "fein":
        return policy.include_fein, "FEIN entered instead of a state number"
    if c == "na":
        return policy.include_na, "Line reads N/A"
    return policy.include_text_entries, f"Text instead of a number ({c.replace('_', ' ')})"


# --- engagement scoring ----------------------------------------------------

def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def _content_key(ex: Extraction) -> str:
    lines = sorted((l.state, _norm(l.id_value)) for l in ex.jurisdiction_lines if l.id_value)
    return json.dumps([_norm(ex.purchaser.name), ex.form_family, lines])


def _parse_date(text: str | None) -> date | None:
    try:
        return date.fromisoformat(text) if text else None
    except ValueError:
        return None


def score_engagement(certs: list[dict], policy: Policy,
                     overrides: dict[int, dict[str, dict]] | None = None,
                     today: date | None = None) -> EngagementResult:
    """Score every certificate. ``certs`` are certificate rows as dicts."""
    overrides = overrides or {}
    today = today or date.today()
    by_id = {c["id"]: c for c in certs}

    extractions: dict[int, Extraction] = {}
    for c in certs:
        source = by_id.get(c["duplicate_of"]) if c.get("duplicate_of") else c
        if source and source.get("extraction_json"):
            extractions[c["id"]] = Extraction.model_validate_json(source["extraction_json"])

    # Content duplicates: same buyer + form + numbers in different files.
    first_by_key: dict[str, int] = {}
    content_dup_of: dict[int, int] = {}
    for c in sorted(certs, key=lambda c: c["id"]):
        ex = extractions.get(c["id"])
        if not ex or c.get("duplicate_of") or not any(l.id_value for l in ex.jurisdiction_lines):
            continue
        key = _content_key(ex)
        if key in first_by_key:
            content_dup_of[c["id"]] = first_by_key[key]
        else:
            first_by_key[key] = c["id"]

    results = [
        _score_certificate(c, extractions.get(c["id"]), policy, overrides.get(c["id"], {}),
                           today, by_id, content_dup_of.get(c["id"]))
        for c in certs
    ]
    return EngagementResult(results)


def _score_certificate(cert: dict, ex: Extraction | None, policy: Policy,
                       overrides: dict[str, dict], today: date, by_id: dict,
                       content_dup_of: int | None) -> CertificateResult:
    fn = json.loads(cert["filename_json"])
    flags: list[Flag] = []
    company = fn.get("company") or (ex.purchaser.name if ex else None) or cert["filename"]
    cert_type = fn.get("cert_type") or (ex.certificate_type.replace("_", " ").title() if ex else "")
    batch = cert.get("batch_label") or ""

    if cert["status"] in ("pending", "processing"):
        return CertificateResult(cert, None, [], [], [Flag("pending", "info", "Not read yet")], [])
    if cert["status"] == "error" or ex is None:
        return CertificateResult(cert, None, [], [], [
            Flag("extraction_error", "review", cert.get("error") or "Could not be read")], [])

    # Duplicates
    suppress = False
    if cert.get("duplicate_of"):
        orig = by_id.get(cert["duplicate_of"], {})
        flags.append(Flag("duplicate_exact", "review",
                          f"Byte-identical to {orig.get('filename', 'another file')}"))
        suppress = not policy.schedule_exact_duplicates
    elif content_dup_of:
        orig = by_id.get(content_dup_of, {})
        flags.append(Flag("duplicate_content", "review",
                          f"Same content as {orig.get('filename', 'another file')} (different file bytes)"))
        suppress = not policy.schedule_content_duplicates

    # Filename vs document
    doc_states = sorted({l.state for l in ex.jurisdiction_lines})
    kind = fn.get("kind")
    basis_single = "single_state"
    if kind == "single" and not ex.is_multistate and doc_states and fn.get("state") not in doc_states:
        flags.append(Flag("filename_state_mismatch", "follow_up",
                          f"Filename says {fn.get('state')} but the document is for {', '.join(doc_states)}; "
                          "rename the file"))
        basis_single = "corrected"
    if kind == "multi" and not ex.is_multistate:
        flags.append(Flag("mislabeled_multistate", "review",
                          "Titled multijurisdiction but the document is a single-state certificate"))
    if kind == "tbd":
        basis_single = "tbd_resolved"
        if doc_states:
            flags.append(Flag("tbd_resolved", "info", f"State resolved from the document: {', '.join(doc_states)}"))

    # Line-level patterns worth a reviewer's eye
    ids = Counter(_norm(l.id_value) for l in ex.jurisdiction_lines if l.id_value)
    repeated = [v for v, n in ids.items() if n >= 3]
    if repeated:
        flags.append(Flag("repeated_number", "review",
                          f"The same number is entered on {max(ids.values())} state lines; "
                          "it is a home-state number or FEIN, not a registration in each state"))
    if any(l.disclaims_registration for l in ex.jurisdiction_lines):
        flags.append(Flag("registration_disclaimed", "review",
                          "One or more lines state the buyer is not registered there (nexus language)"))

    # Entity-based and expiry
    if ex.certificate_type in ("government", "nonprofit", "entity_based") or ex.form_family == "entity_exemption":
        flags.append(Flag("entity_based", "info",
                          f"Entity-based exemption ({ex.exemption_reason or ex.certificate_type}); "
                          "no resale registration expected"))
    exp = _parse_date(ex.expiration_date)
    if exp and exp < today:
        flags.append(Flag("expired", "follow_up", f"Expired on its face ({exp.isoformat()})"))
    elif exp and exp <= today + timedelta(days=policy.expiring_soon_days):
        flags.append(Flag("expiring_soon", "follow_up", f"Expires {exp.isoformat()}"))
    if ex.signed is False:
        flags.append(Flag("unsigned", "follow_up", "Certificate is not signed"))
    if ex.confidence != "high":
        flags.append(Flag("low_confidence", "review", f"Extraction confidence: {ex.confidence}"))
    for obs in ex.observations:
        flags.append(Flag("observation", "review", obs))

    # Lines -> rows
    rows: list[ScheduleRow] = []
    excluded: list[ExcludedLine] = []
    decisions: list[dict] = []
    basis = "exploded" if ex.is_multistate else basis_single
    for line in ex.jurisdiction_lines:
        include, reason = decide_line(line, policy)
        ov = overrides.get(line.state)
        if ov:
            include = ov["decision"] == "include"
            reason = f"Reviewer override: {ov['note'] or ov['decision']}"
        if include and suppress:
            include, reason = False, "Duplicate certificate (policy: not scheduled)"
        decisions.append({"line": line, "include": include, "reason": reason, "override": bool(ov)})
        if include:
            line_flags = []
            if line.classification in ("home_state_number", "fein"):
                line_flags.append(line.classification)
            if _norm(line.id_value) in repeated:
                line_flags.append("repeated_number")
            if line.disclaims_registration:
                line_flags.append("registration_disclaimed")
            rows.append(ScheduleRow(
                certificate_id=cert["id"], company=company, certificate_type=cert_type,
                state=line.state, registration=line.id_value or line.raw_text or None,
                form=ex.form_name, source_file=cert["filename"], batch=batch,
                basis="override" if ov else basis,
                flags=line_flags + [f.code for f in flags
                                    if f.severity != "info" and f.code not in _LINE_LEVEL],
                note=line.note,
            ))
        elif line.classification not in ("blank",):
            excluded.append(ExcludedLine(cert["id"], company, line.state, line.raw_text,
                                         line.classification, reason, cert["filename"]))

    if not rows and not suppress:
        flags.append(Flag("no_qualifying_state", "follow_up",
                          "No state on this certificate carries a qualifying registration number"))
    return CertificateResult(cert, ex, rows, excluded, flags, decisions)
