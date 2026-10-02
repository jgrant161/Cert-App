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
