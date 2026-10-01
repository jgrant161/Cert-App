from certapp.filenames import parse_filename


def test_code_company_type_state():
    info = parse_filename("10423 Acme Supply Co Resale Certificate CA.pdf")
    assert (info.code, info.company, info.cert_type, info.state, info.kind) == (
        "10423", "Acme Supply Co", "Resale Certificate", "CA", "single")


def test_multijurisdiction_token():
    info = parse_filename("A0091 Alfresco Heating Exemption Certificate Multijurisdiction.pdf")
    assert info.kind == "multi" and info.state is None
    assert info.company == "Alfresco Heating"


def test_parenthetical_state_with_company_punctuation():
    info = parse_filename("BE Aerospace (Collins Aerospace) - Resale Certificate (FL).pdf")
    assert info.company == "BE Aerospace (Collins Aerospace)"
    assert info.cert_type == "Resale Certificate"
    assert info.state == "FL" and info.kind == "single"


def test_tbd_state():
    info = parse_filename("Field Aerospace - Resale Certificate (TBD).pdf")
    assert info.kind == "tbd" and info.state is None


def test_full_state_name():
    assert parse_filename("Navy Golf Course - Exemption Certificate (California).pdf").state == "CA"
