"""Filename conventions.

Clients name certificate files in different ways. Two conventions seen so far:

* ``<Internal Code> <Company Name> <Certificate Type> <State>``
  e.g. ``10423 Acme Supply Resale Certificate CA.pdf``
* ``<Company> - <Certificate Type> (<State>)``
  e.g. ``BE Aerospace (Collins) - Resale Certificate (FL).pdf``

The state token may also be ``Multijurisdiction`` (read the grid) or ``TBD``
(read the document). Filename data is only a hint: the document always wins,
and disagreements are flagged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, asdict
from pathlib import PurePath

from .states import MULTI_TOKENS, UNKNOWN_TOKENS, normalize_state

CERT_TYPES = [
    "Resale Certificate", "Exemption Certificate", "Resale", "Exemption",
    "Seller's Permit", "Sellers Permit", "Seller Permit", "Manufacturing Exemption",
    "Direct Pay Permit", "Government Exemption", "Nonprofit Exemption",
    "Tax Exempt Certificate", "Tax Exemption", "Certificate",
]

_TYPE_RE = re.compile(
    "(" + "|".join(re.escape(t) for t in sorted(CERT_TYPES, key=len, reverse=True)) + ")",
    re.IGNORECASE,
)
_TRAILING_COPY = re.compile(r"\s*(\(\d+\)|copy|-\s*copy|_\d+)$", re.IGNORECASE)


@dataclass
class FilenameInfo:
    code: str | None = None
    company: str | None = None
    cert_type: str | None = None
    state: str | None = None          # two-letter code when the filename names one
    state_token: str | None = None    # raw token as written
    kind: str = "unknown"             # single | multi | tbd | unknown

    def as_dict(self) -> dict:
        return asdict(self)


def _classify_token(token: str) -> tuple[str | None, str]:
    t = token.strip().upper()
    if t in MULTI_TOKENS:
        return None, "multi"
    if t in UNKNOWN_TOKENS:
        return None, "tbd"
    code = normalize_state(t)
    if code:
        return code, "single"
    return None, "unknown"


def parse_filename(filename: str) -> FilenameInfo:
    stem = PurePath(filename).stem.strip()
    stem = _TRAILING_COPY.sub("", stem).strip()
    info = FilenameInfo()

    # Convention B: "Company - Type (STATE)" -- state in trailing parentheses.
    m = re.match(r"^(?P<company>.+?)\s+-\s+(?P<type>.+?)\s*\((?P<state>[^()]+)\)\s*(?P<rest>.*)$", stem)
    if m:
        info.company = m.group("company").strip()
        info.cert_type = m.group("type").strip()
        info.state_token = m.group("state").strip()
        info.state, info.kind = _classify_token(info.state_token)
        return info

    # Convention A: "CODE Company Type STATE" -- type phrase splits the name.
    parts = stem.split()
    if not parts:
        return info
    if re.fullmatch(r"[A-Z0-9][A-Z0-9\-_.]*\d[A-Z0-9\-_.]*", parts[0], re.IGNORECASE):
        info.code = parts[0]
        stem = stem[len(parts[0]):].strip()

    last = stem.rsplit(None, 1)
    if len(last) == 2:
        state, kind = _classify_token(last[1])
        if kind != "unknown":
            info.state_token, info.state, info.kind = last[1], state, kind
            stem = last[0]

    tm = None
    for tm in _TYPE_RE.finditer(stem):
        pass  # keep the last match: company names can contain "Supply", "Certificate" rarely
    if tm:
        info.company = stem[: tm.start()].strip(" -_") or None
        info.cert_type = stem[tm.start():].strip(" -_")
    else:
        info.company = stem.strip(" -_") or None
    return info
