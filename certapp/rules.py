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
from functools import lru_cache

from .extraction.schema import Extraction, JurisdictionLine
from .states import SST_ASSOCIATE_MEMBERS, SST_FULL_MEMBERS, normalize_state, parse_state_list


@dataclass
class Policy:
    include_home_state_numbers: bool = True   # home-state number entered on another state's line
    include_fein: bool = True                 # FEIN entered in place of a state number
    include_disclaimed: bool = False          # number + "not registered / exempt per nexus rules"
    include_na: bool = False                  # "N/A"
    include_text_entries: bool = False        # "See attached", "Multiple", "Wayfair Ruling" ...
    schedule_exact_duplicates: bool = False   # byte-identical files under another name
    schedule_content_duplicates: bool = True  # same buyer, form and numbers, different file
    schedule_expired: bool = True             # keep expired certificates on the schedule, marked Expired
    expiring_soon_days: int = 90
    review_if_missing: int = 3                # this many missing items reads "Review for validity"
    home_state_exceptions: str = ""           # states where the home-state setting above is reversed
    fein_exceptions: str = ""                 # states where the FEIN setting above is reversed
    uniform_not_accepted: str = ""            # states that do not take the MTC Uniform certificate
    expiration_rules: str = ""                # one per line: "IL: 3 years", "FL: calendar year", "TX: none"
    customer_aliases: dict = field(default_factory=dict)   # customer key -> key of the customer it joins

    @classmethod
    def from_json(cls, text: str | None) -> "Policy":
        data = json.loads(text or "{}")
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def as_dict(self) -> dict:
        return asdict(self)

    def home_state_ok(self, state: str) -> bool:
        return self.include_home_state_numbers != (state in parse_state_list(self.home_state_exceptions))

    def fein_ok(self, state: str) -> bool:
        return self.include_fein != (state in parse_state_list(self.fein_exceptions))

    def uniform_rejected(self, state: str) -> bool:
        return state in parse_state_list(self.uniform_not_accepted)

    def expiration_rule(self, state: str) -> tuple[str, int | None] | None:
        return parse_expiration_rules(self.expiration_rules)[0].get(state)


POLICY_LABELS = {
    "include_home_state_numbers": "Schedule lines carrying the home-state number",
    "include_fein": "Schedule lines carrying an FEIN instead of a state number",
    "include_disclaimed": "Schedule lines whose text disclaims registration (e.g. \"exempt per nexus rules\")",
    "include_na": "Schedule lines reading \"N/A\"",
    "include_text_entries": "Schedule lines with text instead of a number (\"See attached\", \"Multiple\", \"Wayfair Ruling\")",
    "schedule_exact_duplicates": "Schedule byte-identical duplicate files",
    "schedule_content_duplicates": "Schedule certificates whose content duplicates another file",
    "schedule_expired": "Keep expired certificates on the schedule, marked Expired (unticked: they move to Excluded lines)",
}

TEXT_SETTINGS = ("home_state_exceptions", "fein_exceptions", "uniform_not_accepted", "expiration_rules")


@lru_cache(maxsize=64)
def parse_expiration_rules(text: str) -> tuple[dict[str, tuple[str, int | None]], tuple[str, ...]]:
    """Parse the policy's state expiration rules. Returns ({state: (kind, years)}, unreadable lines).

    kind is "years" (that many years from the signature date), "calendar" (December 31 of the
    year signed) or "none" (no expiration).
    """
    rules: dict[str, tuple[str, int | None]] = {}
    bad: list[str] = []
    for raw in re.split(r"[\n;]+", text or ""):
        line = raw.strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z]{2})\b\s*[:=\-]?\s*(.*)$", line)
        state = normalize_state(m.group(1)) if m else None
        rest = (m.group(2) if m else "").lower()
        years = re.match(r"(\d+)", rest)
        if state and years and 0 < int(years.group(1)) <= 50:
            rules[state] = ("years", int(years.group(1)))
        elif state and any(w in rest for w in ("calendar", "dec", "year end", "year-end", "annual")):
            rules[state] = ("calendar", None)
        elif state and any(w in rest for w in ("none", "never", "no exp", "not expire", "revoked")):
            rules[state] = ("none", None)
        else:
            bad.append(line)
    return rules, tuple(bad)


def describe_rule(rule: tuple[str, int | None]) -> str:
    kind, years = rule
    if kind == "years":
        return f"{years} year{'s' if years != 1 else ''} from the signature date"
    return "December 31 of the year signed" if kind == "calendar" else "No expiration"


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
    form_number: str | None
    form_name: str | None
    source_file: str
    batch: str
    basis: str      # single_state | exploded | tbd_resolved | corrected | override | exemption_type
    flags: list[str] = field(default_factory=list)
    note: str | None = None
    status: str = "Valid"                 # Valid | Invalid | Expired | Pending
    status_reason: str = ""
    customer: str = ""                    # the merged customer this certificate belongs to
    name_on_certificate: str | None = None
    signed: bool | None = None
    signature_date: str | None = None
    expires: str | None = None            # stated, or worked out from the state's rule
    expiration_basis: str = ""
    exemption_reason: str | None = None
    purchaser_address: str | None = None
    seller_on_certificate: str | None = None
    seller_match: str = ""                # Match | Does not match | Not stated | ""
    review_status: str = ""
    reviewer_note: str | None = None
    completeness: str = ""                # Valid | Missing: ... | Review for validity: ...
    product_description: str | None = None


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
    status: str = "Pending"
    status_reason: str = ""
    customer: str = ""
    company: str = ""
    seller_match: str = ""
    other_pages: list[dict] = field(default_factory=list)
    split_into: int = 0           # further certificates found in this file
    completeness: str = ""
    missing: list[str] = field(default_factory=list)


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
        """Customers with the states they are scheduled in (name variants merged)."""
        states: dict[str, set] = {}
        for r in self.rows:
            states.setdefault(r.customer or r.company, set()).add(r.state)
        return sorted(((c, len(s), ", ".join(sorted(s))) for c, s in states.items()),
                      key=lambda t: t[0].lower())

    def customers(self) -> list[dict]:
        """One entry per customer: the names it appears under and its certificates."""
        out: dict[str, dict] = {}
        for c in self.certificates:
            entry = out.setdefault(c.customer, {"name": c.customer, "variants": set(), "name_keys": set(),
                                                "certificates": [], "states": set(), "statuses": Counter()})
            entry["variants"].add(c.company)
            if c.extraction and c.extraction.purchaser.name:
                entry["variants"].add(c.extraction.purchaser.name)
            entry["name_keys"].add(customer_key(c.company))
            entry["certificates"].append(c)
            entry["states"].update(r.state for r in c.rows)
            entry["statuses"][c.status] += 1
        for e in out.values():
            e["key"] = customer_key(e["name"])
            e["variants"] = sorted(e["variants"], key=str.lower)
        return sorted(out.values(), key=lambda e: e["name"].lower())

    def status_counts(self) -> Counter:
        return Counter(c.status for c in self.certificates)

    def flag_counts(self) -> Counter:
        return Counter(f.severity for c in self.certificates for f in c.flags)


# --- line decisions --------------------------------------------------------

# Certificate flags that describe particular lines; rows carry them only when they apply.
_LINE_LEVEL = {"repeated_number", "registration_disclaimed", "expired", "expiring_soon", "form_not_accepted"}

_ALWAYS_OUT = {"blank": "Line left blank", "dash": "Line dashed out"}

# Exemptions that rest on who the buyer is or how the goods are used, not on resale, so the
# resale registration-number test does not apply.
NON_RESALE_TYPES = {"manufacturing", "government", "nonprofit", "agricultural", "direct_pay", "entity_based"}

# A certificate with one of these is not valid as it stands.
INVALID_CODES = {"unsigned", "seller_mismatch", "missing_purchaser", "no_qualifying_state"}
# Reviewer checks that do not hold up a certificate's status.
NOT_PENDING = {"non_certificate_pages"}
STATUS_ORDER = ["Invalid", "Expired", "Pending", "Valid"]


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
        ok = policy.home_state_ok(line.state)
        return ok, "Home-state number used on this line" + ("" if ok else f" (not accepted for {line.state})")
    if c == "fein":
        ok = policy.fein_ok(line.state)
        return ok, "FEIN entered instead of a state number" + ("" if ok else f" (not accepted for {line.state})")
    if c == "na":
        return policy.include_na, "Line reads N/A"
    return policy.include_text_entries, f"Text instead of a number ({c.replace('_', ' ')})"


# --- names -------------------------------------------------------------------

_SUFFIXES = {"inc", "incorporated", "llc", "corp", "corporation", "co", "company", "ltd", "limited",
             "lp", "llp", "pllc", "pc", "plc", "na"}


def customer_key(name: str | None) -> str:
    """'ACME Supply, Inc.' and 'Acme Supply' -> 'acme supply'. Used to merge name variants."""
    text = (name or "").lower().replace("&", " and ")
    text = re.split(r"\b(?:dba|d/b/a|d b a)\b", text)[0]
    text = re.sub(r"\bl\.?\s?l\.?\s?c\b\.?", "llc", text)
    words = re.sub(r"[^a-z0-9]+", " ", text).split()
    if words and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in _SUFFIXES:
        words = words[:-1]
    return " ".join(words)


def _same_party(a: str | None, b: str | None) -> bool:
    ka, kb = customer_key(a), customer_key(b)
    return bool(ka and kb) and (ka == kb or ka in kb or kb in ka)


# --- completeness ----------------------------------------------------------

def _blank_field_name(text: str) -> str | None:
    """Put a blank field the reader reported into the app's own words; None to ignore it."""
    t = text.lower().strip()
    if not t or "expir" in t:                 # no expiration date is normal
        return None
    if "seller" in t or "vendor" in t:
        return "seller address" if "address" in t else "seller name"
    if "date" in t:
        return "date signed"
    if "sign" in t:
        return "signature"
    if "address" in t:
        return "buyer address"
    if "name" in t and any(w in t for w in ("purchaser", "buyer", "customer", "business", "company")):
        return "buyer name"
    if any(w in t for w in ("registration", "permit", "account", "license", "id number", "tax id", "number")):
        return "registration number"
    if any(w in t for w in ("reason", "exemption type", "basis", "type of exemption")):
        return "exemption reason"
    if any(w in t for w in ("description", "property", "product", "item", "goods", "merchandise")):
        return "product description"
    return t[:60]


def missing_items(ex: Extraction, non_resale: bool) -> list[str]:
    """What the form is missing: checked from the reading, plus blank fields the reader reported."""
    missing = []
    if ex.signed is False:
        missing.append("signature")
    elif ex.signed is None:
        missing.append("signature (unclear)")
    if ex.signed is not False and not ex.signature_date:
        missing.append("date signed")
    if not ex.purchaser.name:
        missing.append("buyer name")
    if not ex.purchaser.address:
        missing.append("buyer address")
    if not ex.seller.name:
        missing.append("seller name")
    if not non_resale and not any(l.id_value for l in ex.jurisdiction_lines):
        missing.append("registration number")
    if non_resale and not ex.exemption_reason:
        missing.append("exemption reason")
    for raw in ex.blank_fields:
        name = _blank_field_name(raw)
        if name == "signature" and "signature (unclear)" in missing:
            missing[missing.index("signature (unclear)")] = "signature"
            continue
        if name and name not in missing:
            missing.append(name)
    return missing


def completeness_label(missing: list[str], policy: Policy) -> str:
    if not missing:
        return "Valid"
    if len(missing) >= max(1, policy.review_if_missing):
        return "Review for validity: missing " + ", ".join(missing)
    return "Missing: " + ", ".join(missing)


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


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:                       # February 29
        return d.replace(year=d.year + years, day=28)


def expiration_for(ex: Extraction, state: str, policy: Policy) -> tuple[date | None, str, bool]:
    """(expiration date, where it comes from, undetermined) for one state on a certificate."""
    stated = _parse_date(ex.expiration_date)
    if stated:
        return stated, "Stated on the certificate", False
    rule = policy.expiration_rule(state)
    if not rule:
        return None, "", False
    if rule[0] == "none":
        return None, f"{state} rule: no expiration", False
    signed = _parse_date(ex.signature_date)
    if not signed:
        return None, f"{state} rule ({describe_rule(rule)}) needs a signature date", True
    if rule[0] == "years":
        return _add_years(signed, rule[1]), f"{state} rule: {describe_rule(rule)}", False
    return date(signed.year, 12, 31), f"{state} rule: {describe_rule(rule)}", False


def score_engagement(certs: list[dict], policy: Policy,
                     overrides: dict[int, dict[str, dict]] | None = None,
                     today: date | None = None, seller_name: str | None = None) -> EngagementResult:
    """Score every certificate. ``certs`` are certificate rows as dicts."""
    overrides = overrides or {}
    today = today or date.today()
    by_id = {c["id"]: c for c in certs}
    children = Counter(c["parent_id"] for c in certs if c.get("parent_id"))

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
                           today, by_id, content_dup_of.get(c["id"]), seller_name, children.get(c["id"], 0))
        for c in certs
    ]
    _merge_customers(results, policy)
    return EngagementResult(results)


def _merge_customers(results: list[CertificateResult], policy: Policy) -> None:
    """Group name variants ('Acme Supply Inc.' / 'ACME Supply') and reviewer merges into one customer."""
    def target(key: str) -> str:
        seen = set()
        while key in policy.customer_aliases and key not in seen:
            seen.add(key)
            key = policy.customer_aliases[key]
        return key

    names: dict[str, Counter] = {}
    for r in results:
        names.setdefault(target(customer_key(r.company)), Counter())[r.company] += 1
    display = {k: max(c.items(), key=lambda kv: (kv[1], len(kv[0])))[0] for k, c in names.items()}
    for r in results:
        r.customer = display[target(customer_key(r.company))]
        for row in r.rows:
            row.customer = r.customer


def _worst(statuses: list[tuple[str, str]]) -> tuple[str, str]:
    return min(statuses, key=lambda s: STATUS_ORDER.index(s[0]))


def _score_certificate(cert: dict, ex: Extraction | None, policy: Policy,
                       overrides: dict[str, dict], today: date, by_id: dict,
                       content_dup_of: int | None, seller_name: str | None = None,
                       split_into: int = 0) -> CertificateResult:
    fn = json.loads(cert["filename_json"])
    flags: list[Flag] = []
    company = fn.get("company") or (ex.purchaser.name if ex else None) or cert["filename"]
    cert_type = fn.get("cert_type") or (ex.certificate_type.replace("_", " ").title() if ex else "")
    batch = cert.get("batch_label") or ""
    other_pages = json.loads(cert.get("other_pages_json") or "[]")

    if cert["status"] in ("pending", "processing"):
        return CertificateResult(cert, None, [], [], [Flag("pending", "info", "Not read yet")], [],
                                 "Pending", "Not read yet", company=company)
    if cert["status"] == "error" or ex is None:
        return CertificateResult(cert, None, [], [], [
            Flag("extraction_error", "review", cert.get("error") or "Could not be read")], [],
            "Pending", "Could not be read", company=company)

    # Where the certificate came from
    if cert.get("parent_id"):
        parent = by_id.get(cert["parent_id"], {})
        flags.append(Flag("split", "info", f"Split out of {parent.get('filename', 'a bundled file')}"
                                           + (f", pages {cert['pages']}" if cert.get("pages") else "")))
    if split_into:
        flags.append(Flag("split", "info", f"This file held {split_into + 1} certificates; each has its own row"
                                           + (f" (this one is pages {cert['pages']})" if cert.get("pages") else "")))
    if other_pages:
        what = "; ".join(f"page{'s' if len(o['pages']) > 1 else ''} {', '.join(map(str, o['pages']))}: "
                         f"{o['description']}" for o in other_pages)
        flags.append(Flag("non_certificate_pages", "review", f"Not certificates, left off the schedule: {what}"))

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
    if cert.get("parent_id"):
        kind = None                          # the filename describes the whole bundle, not this part
    if kind == "single" and not ex.is_multistate and doc_states and fn.get("state") not in doc_states:
        flags.append(Flag("filename_state_mismatch", "follow_up",
                          f"Filename says {fn.get('state')} but the document is for {', '.join(doc_states)}; "
                          "rename the file"))
        basis_single = "corrected"
    if kind == "multi" and not ex.is_multistate and not split_into:
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

    # Exemption type
    non_resale = ex.certificate_type in NON_RESALE_TYPES or ex.form_family == "entity_exemption"
    type_label = ex.certificate_type.replace("_", " ")
    if ex.certificate_type in ("government", "nonprofit", "entity_based") or ex.form_family == "entity_exemption":
        flags.append(Flag("entity_based", "info",
                          f"Entity-based exemption ({ex.exemption_reason or ex.certificate_type}); "
                          "no resale registration expected"))
    elif non_resale:
        flags.append(Flag("exemption_type", "info",
                          f"{type_label.capitalize()} exemption; the resale registration-number test does not apply"))
    if non_resale and not ex.exemption_reason:
        flags.append(Flag("missing_exemption_reason", "review", "No exemption reason or legal basis is stated"))

    # The document itself (skipped for filename-only demo readings, which have nothing to check)
    real_read = cert.get("extractor") != "demo"
    seller_match = ""
    if ex.signed is False:
        flags.append(Flag("unsigned", "follow_up", "Certificate is not signed"))
    if real_read:
        if not ex.purchaser.name:
            flags.append(Flag("missing_purchaser", "follow_up", "The purchaser's name is not filled in"))
        elif not ex.purchaser.address:
            flags.append(Flag("missing_purchaser_address", "follow_up", "The purchaser's address is not filled in"))
        if seller_name:
            if not ex.seller.name:
                seller_match = "Not stated"
            elif _same_party(ex.seller.name, seller_name):
                seller_match = "Match"
            else:
                seller_match = "Does not match"
                flags.append(Flag("seller_mismatch", "follow_up",
                                  f"Made out to {ex.seller.name}, not {seller_name}"))
    missing = missing_items(ex, non_resale) if real_read else []
    completeness = completeness_label(missing, policy) if real_read else "Not checked (filled in from the filename)"
    if "signature" in missing and ex.signed is not False:      # the reader saw an empty signature line
        flags.append(Flag("unsigned", "follow_up", "Certificate is not signed"))
    already = {"signature", "buyer name", "buyer address", "exemption reason"}   # flagged on their own
    others = [m for m in missing if m not in already and not (m == "registration number" and not non_resale)]
    if others:
        flags.append(Flag("incomplete_form", "follow_up", "Missing on the form: " + ", ".join(others)))
    stated = _parse_date(ex.expiration_date)
    if stated and stated < today:
        flags.append(Flag("expired", "follow_up", f"Expired on its face ({stated.isoformat()})"))
    elif stated and stated <= today + timedelta(days=policy.expiring_soon_days):
        flags.append(Flag("expiring_soon", "follow_up", f"Expires {stated.isoformat()}"))
    if ex.confidence != "high":
        flags.append(Flag("low_confidence", "review", f"Extraction confidence: {ex.confidence}"))
    if ex.field_confidence:
        low = [k.replace("_", " ") for k, v in ex.field_confidence.model_dump().items() if v == "low"]
        if low:
            flags.append(Flag("uncertain_fields", "review", f"Hard to read, check by eye: {', '.join(low)}"))
    for obs in ex.observations:
        flags.append(Flag("observation", "review", obs))

    # Lines -> rows
    rows: list[ScheduleRow] = []
    excluded: list[ExcludedLine] = []
    decisions: list[dict] = []
    basis = "exploded" if ex.is_multistate else basis_single
    lines = list(ex.jurisdiction_lines)
    if not lines and non_resale and ex.issuing_state:
        lines = [JurisdictionLine(state=ex.issuing_state, raw_text="", id_value=None, classification="blank",
                                  id_issuing_state=None, disclaims_registration=False, note=None)]
    reviewed = cert.get("review_status") == "reviewed"
    pending = [f.message for f in flags if f.severity == "review" and f.code not in NOT_PENDING]
    if not real_read:
        pending.insert(0, "Filled in from the filename; not read")
    elif missing:
        pending.append(completeness)
    cert_invalid = [f.message for f in flags if f.code in INVALID_CODES]
    row_statuses: list[tuple[str, str]] = []
    kept_off: list[tuple[str, str]] = []      # states held off the schedule only because they expired

    for line in lines:
        include, reason = decide_line(line, policy)
        line_basis = basis
        waived = (non_resale and not ex.is_multistate and not include and line.classification != "dash"
                  and not line.disclaims_registration)
        if waived:
            include, reason, line_basis = True, f"No resale registration needed for a {type_label} exemption", \
                "exemption_type"
        ov = overrides.get(line.state)
        if ov:
            include = ov["decision"] == "include"
            reason = f"Reviewer override: {ov['note'] or ov['decision']}"

        # Validity for this state
        row_flags: list[str] = []
        invalid = list(cert_invalid)
        if ex.form_family == "sst_streamlined" and line.state not in SST_FULL_MEMBERS | SST_ASSOCIATE_MEMBERS:
            invalid.append(f"Streamlined (SST) certificate used for {line.state}, which is not an SST member state")
        if ex.form_family == "mtc_uniform" and policy.uniform_rejected(line.state):
            invalid.append(f"{line.state} does not accept the MTC Uniform certificate (review policy)")
        expires, exp_basis, undetermined = expiration_for(ex, line.state, policy)
        if include and len(invalid) > len(cert_invalid):
            row_flags.append("form_not_accepted")
            flags.append(Flag("form_not_accepted", "follow_up", invalid[-1]))
        expired = bool(expires and expires < today)
        if include and expires and not stated:       # rule-based dates are flagged per state
            if expired:
                flags.append(Flag("expired", "follow_up", f"{line.state}: expired {expires.isoformat()} ({exp_basis})"))
            elif expires <= today + timedelta(days=policy.expiring_soon_days):
                flags.append(Flag("expiring_soon", "follow_up",
                                  f"{line.state}: expires {expires.isoformat()} ({exp_basis})"))
        if expired:
            row_flags.append("expired")
        elif expires and expires <= today + timedelta(days=policy.expiring_soon_days):
            row_flags.append("expiring_soon")
        row_pending = ([exp_basis] if undetermined and not reviewed else []) + ([] if reviewed else list(pending))
        if cert.get("status_override") in ("valid", "invalid"):
            status = (cert["status_override"].title(), "Set by reviewer")
        elif invalid:
            status = ("Invalid", invalid[0] if len(invalid) == len(cert_invalid) else invalid[-1])
        elif expired:
            status = ("Expired", f"Expired {expires.isoformat()}")
        elif row_pending:
            status = ("Pending", row_pending[0])
        else:
            status = ("Valid", "")

        if include and suppress:
            include, reason = False, "Duplicate certificate (policy: not scheduled)"
        if include and status[0] == "Expired" and not policy.schedule_expired and not ov:
            include, reason = False, "Expired (policy: expired certificates are kept off the schedule)"
            kept_off.append(status)
        decisions.append({"line": line, "include": include, "reason": reason, "override": bool(ov),
                          "status": status[0], "expires": expires.isoformat() if expires else None,
                          "expiration_basis": exp_basis})
        if include:
            row_statuses.append(status)
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
                form_number=ex.form_number, form_name=ex.form_name, source_file=cert["filename"], batch=batch,
                basis="override" if ov else line_basis,
                flags=line_flags + row_flags + [f.code for f in flags
                                                if f.severity != "info" and f.code not in _LINE_LEVEL],
                note=line.note, status=status[0], status_reason=status[1],
                name_on_certificate=ex.purchaser.name, signed=ex.signed, signature_date=ex.signature_date,
                expires=expires.isoformat() if expires else None, expiration_basis=exp_basis,
                exemption_reason=ex.exemption_reason, purchaser_address=ex.purchaser.address,
                seller_on_certificate=ex.seller.name, seller_match=seller_match,
                review_status=cert.get("review_status") or "", reviewer_note=cert.get("reviewer_note"),
                completeness=completeness, product_description=ex.property_description,
            ))
        elif line.classification not in ("blank",):
            excluded.append(ExcludedLine(cert["id"], company, line.state, line.raw_text,
                                         line.classification, reason, cert["filename"]))

    if not rows and not suppress and not kept_off:
        flags.append(Flag("no_qualifying_state", "follow_up",
                          f"No state could be scheduled from this {type_label} certificate" if non_resale else
                          "No state on this certificate carries a qualifying registration number"))
        cert_invalid.append(flags[-1].message)

    # The certificate's own status: its worst state, or its own problems when nothing was scheduled.
    if cert.get("status_override") in ("valid", "invalid"):
        status = (cert["status_override"].title(), "Set by reviewer")
    elif row_statuses or kept_off:
        status = _worst(row_statuses + kept_off)
    elif cert_invalid:
        status = ("Invalid", cert_invalid[0])
    elif stated and stated < today:
        status = ("Expired", f"Expired {stated.isoformat()}")
    elif pending and not reviewed:
        status = ("Pending", pending[0])
    else:
        status = ("Valid", "")
    return CertificateResult(cert, ex, rows, excluded, _dedupe(flags), decisions, status[0], status[1],
                             company=company, seller_match=seller_match, other_pages=other_pages,
                             split_into=split_into, completeness=completeness, missing=missing)


def _dedupe(flags: list[Flag]) -> list[Flag]:
    seen, out = set(), []
    for f in flags:
        if (f.code, f.message) not in seen:
            seen.add((f.code, f.message))
            out.append(f)
    return out
