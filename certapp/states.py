"""US jurisdiction reference data."""

STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "PR": "Puerto Rico",
}

_BY_NAME = {name.upper(): code for code, name in STATES.items()}

# Tokens used in filenames to mean "read the document to find the state(s)".
MULTI_TOKENS = {"MULTIJURISDICTION", "MULTI-JURISDICTION", "MULTI JURISDICTION",
                "MULTISTATE", "MULTI-STATE", "MULTI STATE", "MULTIPLE", "MTC",
                "SST", "STREAMLINED", "UNIFORM"}
UNKNOWN_TOKENS = {"TBD", "UNKNOWN", "?"}


def normalize_state(token: str | None) -> str | None:
    """Return a two-letter code for a code or full state name, else None."""
    if not token:
        return None
    t = token.strip().upper().replace(".", "")
    if t in STATES:
        return t
    return _BY_NAME.get(t)

# Streamlined Sales Tax members, from the Wisconsin Department of Revenue SST page and the SST
# Governing Board's 2026 compliance notice (checked October 2026). Tennessee is an associate member.
SST_FULL_MEMBERS = frozenset({
    "AR", "GA", "IN", "IA", "KS", "KY", "MI", "MN", "NE", "NV", "NJ", "NC", "ND", "OH", "OK",
    "RI", "SD", "UT", "VT", "WA", "WV", "WI", "WY",
})
SST_ASSOCIATE_MEMBERS = frozenset({"TN"})


def parse_state_list(text: str | None) -> list[str]:
    """"AL, ct; Florida" -> ["AL", "CT", "FL"]. Unrecognised entries are dropped."""
    import re
    found = {normalize_state(t) for t in re.split(r"[,;\n]+|\s{2,}", text or "") if t.strip()}
    if not found - {None} and text and text.strip():   # space-separated codes: "AL CT FL"
        found = {normalize_state(t) for t in text.split()}
    return sorted(s for s in found if s)
