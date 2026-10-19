import json
from datetime import date

from certapp.filenames import parse_filename
from certapp.rules import Policy, score_engagement

from conftest import extraction, line

TODAY = date(2026, 10, 1)


def cert(cid, filename, ex, *, status="extracted", duplicate_of=None):
    return {"id": cid, "filename": filename, "status": status, "batch_label": "B1",
            "filename_json": json.dumps(parse_filename(filename).as_dict()),
            "extraction_json": ex.model_dump_json() if ex else None, "error": None,
            "duplicate_of": duplicate_of, "review_status": "open", "reviewer_note": None}


def score(certs, policy=None, overrides=None):
    return score_engagement(certs, policy or Policy(), overrides, today=TODAY)


def states(result, cid=None):
    return sorted(r.state for r in result.rows if cid is None or r.certificate_id == cid)


def codes(result, cid):
    return {f.code for c in result.certificates if c.cert["id"] == cid for f in c.flags}


def test_multistate_grid_explodes_one_row_per_numbered_state():
    ex = extraction([line("CA", "SR-123"), line("TX", "3201"), line("NV", "", "blank"),
                     line("AZ", "N/A", "na"), line("FL", "-", "dash")], multistate=True)
    r = score([cert(1, "A1 Patio Co Resale Certificate Multijurisdiction.pdf", ex)])
    assert states(r) == ["CA", "TX"]
    assert {x.state for x in r.excluded} == {"AZ", "FL"}   # blanks are not listed


def test_na_policy_toggle_flips_without_rereading():
    ex = extraction([line("CA", "SR-1"), line("AZ", "N/A", "na")], multistate=True)
    c = [cert(1, "A1 Yaskawa Resale Certificate Multijurisdiction.pdf", ex)]
    assert states(score(c)) == ["CA"]
    assert states(score(c, Policy(include_na=True))) == ["AZ", "CA"]


def test_nexus_disclaimer_lines_are_excluded_by_default():
    # Alfresco Heating: CA permit + "& Exempt Per Nexus Rules" on every line.
    lines = [line("CA", "SR-555", "registration_number")] + [
        line(s, "SR-555 & Exempt Per Nexus Rules", "home_state_number", id_value="SR-555",
             issuing="CA", disclaims=True) for s in ("AZ", "CO", "TX", "WA")]
    ex = extraction(lines, multistate=True)
    c = [cert(1, "A9 Alfresco Heating Exemption Certificate Multijurisdiction.pdf", ex)]
    r = score(c)
    assert states(r) == ["CA"]
    assert {"repeated_number", "registration_disclaimed"} <= codes(r, 1)
    assert len(states(score(c, Policy(include_disclaimed=True)))) == 5


def test_home_state_number_lines_included_and_flagged():
    # Brandster: Florida number used on AL/CT lines, labelled as the home-state number.
    ex = extraction([line("FL", "FL-77"), line("AL", "FL-77", "home_state_number", issuing="FL"),
                     line("CT", "FL-77", "home_state_number", issuing="FL")], multistate=True)
    r = score([cert(1, "B2 Brandster Resale Certificate Multijurisdiction.pdf", ex)])
    assert states(r) == ["AL", "CT", "FL"]
    assert "home_state_number" in next(x for x in r.rows if x.state == "AL").flags
    off = score([cert(1, "B2 Brandster Resale Certificate Multijurisdiction.pdf", ex)],
                Policy(include_home_state_numbers=False))
    assert states(off) == ["FL"]


def test_text_entries_are_not_registrations():
    ex = extraction([line("CO", "CO-1"), line("AZ", "Wayfair Ruling", "non_numeric_text"),
                     line("GA", "SEE ATTACHED", "see_attached")], multistate=True)
    r = score([cert(1, "M1 Modern Living Spaces Resale Certificate Multijurisdiction.pdf", ex)])
    assert states(r) == ["CO"]


def test_no_qualifying_state_is_a_client_follow_up():
    ex = extraction([line("CA", "", "blank"), line("TX", "", "blank")], multistate=True,
                    family="sst_streamlined")
    r = score([cert(1, "P1 Patio Comforts Resale Certificate Multijurisdiction.pdf", ex)])
    assert not r.rows
    assert "no_qualifying_state" in codes(r, 1)


def test_filename_state_corrected_from_document():
    # Collins: filename says FL, document is NC Form E-595E.
    ex = extraction([line("NC", "600123")], form="E-595E")
    r = score([cert(1, "BE Aerospace (Collins Aerospace) - Resale Certificate (FL).pdf", ex)])
    assert states(r) == ["NC"]
    assert r.rows[0].basis == "corrected"
    assert "filename_state_mismatch" in codes(r, 1)


def test_tbd_resolved_and_mislabeled_multistate():
    tbd = extraction([line("OK", "OK-1")])
    single = extraction([line("CA", "SR-9")], form="CDTFA-230")
    r = score([cert(1, "Field Aerospace - Resale Certificate (TBD).pdf", tbd),
               cert(2, "F1 The Fireplace Element Resale Certificate Multijurisdiction.pdf", single)])
    assert next(x for x in r.rows if x.certificate_id == 1).basis == "tbd_resolved"
    assert "mislabeled_multistate" in codes(r, 2)
    assert states(r, 2) == ["CA"]


def test_exact_duplicates_linked_not_scheduled_and_content_duplicates_flagged():
    ex = extraction([line("TX", "32000")], purchaser="Ametek")
    r = score([cert(1, "Ametek - Resale Certificate (TX).pdf", ex),
               cert(2, "Ametek Inc - Resale Certificate (TX).pdf", None, duplicate_of=1),
               cert(3, "Ametek copy2 - Resale Certificate (TX).pdf", ex)])
    assert states(r, 1) == ["TX"]
    assert states(r, 2) == [] and "duplicate_exact" in codes(r, 2)
    assert states(r, 3) == ["TX"] and "duplicate_content" in codes(r, 3)
    r2 = score([cert(1, "Ametek - Resale Certificate (TX).pdf", ex),
                cert(3, "Ametek copy2 - Resale Certificate (TX).pdf", ex)],
               Policy(schedule_content_duplicates=False))
    assert states(r2, 3) == [] and "no_qualifying_state" not in codes(r2, 3)


def test_expired_expiring_unsigned_and_entity_based():
    r = score([
        cert(1, "Woods Hole - Exemption Certificate (MA).pdf",
             extraction([line("MA", "E-1")], expires="2021-06-30", cert_type="nonprofit")),
        cert(2, "Acme - Resale Certificate (WA).pdf", extraction([line("WA", "6000")], expires="2026-11-15")),
        cert(3, "Beta - Resale Certificate (CA).pdf", extraction([line("CA", "SR-2")], signed=False)),
    ])
    assert {"expired", "entity_based"} <= codes(r, 1)
    assert "expiring_soon" in codes(r, 2)
    assert "unsigned" in codes(r, 3)


def test_reviewer_override_beats_policy():
    ex = extraction([line("CA", "SR-1"), line("AZ", "N/A", "na")], multistate=True)
    c = [cert(1, "A1 Yaskawa Resale Certificate Multijurisdiction.pdf", ex)]
    r = score(c, overrides={1: {"AZ": {"decision": "include", "note": "client confirmed"}}})
    assert states(r) == ["AZ", "CA"]
    assert next(x for x in r.rows if x.state == "AZ").basis == "override"


def test_pending_and_error_certificates_produce_no_rows():
    r = score([cert(1, "X - Resale Certificate (CA).pdf", None, status="pending"),
               {**cert(2, "Y - Resale Certificate (CA).pdf", None, status="error"), "error": "bad pdf"}])
    assert not r.rows
    assert "extraction_error" in codes(r, 2)


# --- validity (status, expiration, form acceptance, exemption types) -------

from certapp.extraction.schema import FieldConfidence, Party  # noqa: E402
from certapp.rules import customer_key, parse_expiration_rules  # noqa: E402


def full(ex, **update):
    """A complete, clean certificate: signed, dated, purchaser address and seller filled in."""
    base = {"purchaser": Party(name=ex.purchaser.name, address="1 Main St, Reno, NV", state="NV"),
            "seller": Party(name="Demo Seller, LLC", address=None, state=None), "signature_date": "2024-03-01"}
    return ex.model_copy(update={**base, **update})


def status_of(result, cid):
    return next(c for c in result.certificates if c.cert["id"] == cid).status


def row(result, state):
    return next(r for r in result.rows if r.state == state)


def test_clean_certificate_is_valid_and_carries_validity_fields():
    ex = full(extraction([line("CA", "SR-1")], purchaser="Acme Supply Inc."), exemption_reason="Resale")
    r = score_engagement([cert(1, "Acme - Resale Certificate (CA).pdf", ex)], Policy(), today=TODAY,
                         seller_name="Demo Seller LLC")
    ca = row(r, "CA")
    assert (ca.status, ca.signed, ca.signature_date, ca.seller_match) == ("Valid", True, "2024-03-01", "Match")
    assert ca.purchaser_address == "1 Main St, Reno, NV" and ca.expires is None


def test_status_invalid_expired_pending():
    c = [cert(1, "A - Resale Certificate (CA).pdf", full(extraction([line("CA", "SR-1")]), signed=False)),
         cert(2, "B - Resale Certificate (CA).pdf", full(extraction([line("CA", "SR-2")], expires="2025-01-31"))),
         cert(3, "C - Resale Certificate (CA).pdf",
              full(extraction([line("CA", "SR-3")], observations=["Faint scan"]))),
         cert(4, "D - Resale Certificate (CA).pdf", None, status="pending")]
    r = score(c)
    assert [status_of(r, i) for i in (1, 2, 3, 4)] == ["Invalid", "Expired", "Pending", "Pending"]
    assert next(x for x in r.rows if x.certificate_id == 2).status_reason == "Expired 2025-01-31"


def test_reviewing_clears_pending_and_override_sets_status():
    ex = full(extraction([line("CA", "SR-3")], observations=["Faint scan"]))
    reviewed = {**cert(1, "C - Resale Certificate (CA).pdf", ex), "review_status": "reviewed"}
    assert status_of(score([reviewed]), 1) == "Valid"
    unsigned = {**cert(2, "U - Resale Certificate (CA).pdf", full(ex, signed=False)), "status_override": "valid"}
    r = score([unsigned])
    assert status_of(r, 2) == "Valid" and r.rows[0].status_reason == "Set by reviewer"


def test_seller_mismatch_is_invalid():
    ex = full(extraction([line("CA", "SR-1")]), seller=Party(name="Someone Else Inc", address=None, state=None))
    r = score_engagement([cert(1, "A - Resale Certificate (CA).pdf", ex)], Policy(), today=TODAY,
                         seller_name="Demo Seller, LLC")
    assert status_of(r, 1) == "Invalid" and "seller_mismatch" in codes(r, 1)
    assert r.rows[0].seller_match == "Does not match"


def test_state_expiration_rules_when_no_date_is_stated():
    rules, bad = parse_expiration_rules("IL: 3 years\nFL calendar year\nTX: none\nZZ: 5 years\nhello")
    assert rules == {"IL": ("years", 3), "FL": ("calendar", None), "TX": ("none", None)}
    assert bad == ("ZZ: 5 years", "hello")
    ex = full(extraction([line("IL", "1234-5678"), line("FL", "78-1"), line("TX", "3201"), line("OH", "9")],
                         multistate=True), signature_date="2023-03-01")
    p = Policy(expiration_rules="IL: 3 years\nFL: calendar year\nTX: none")
    r = score([cert(1, "A Resale Certificate Multijurisdiction.pdf", ex)], p)
    assert (row(r, "IL").expires, row(r, "IL").status) == ("2026-03-01", "Expired")
    assert (row(r, "FL").expires, row(r, "FL").status) == ("2023-12-31", "Expired")
    assert (row(r, "TX").expires, row(r, "TX").status, row(r, "TX").expiration_basis) == (None, "Valid", "TX rule: no expiration")
    assert row(r, "OH").status == "Valid"
    assert "expired" in row(r, "IL").flags and "expired" not in row(r, "OH").flags
    assert status_of(r, 1) == "Expired"
    # A rule that needs a signature date it does not have leaves the state pending.
    r2 = score([cert(1, "A Resale Certificate Multijurisdiction.pdf", full(ex, signature_date=None))], p)
    assert row(r2, "IL").status == "Pending" and "needs a signature date" in row(r2, "IL").status_reason


def test_expired_certificates_can_be_kept_off_the_schedule():
    ex = full(extraction([line("CA", "SR-1")], expires="2025-01-31"))
    c = [cert(1, "A - Resale Certificate (CA).pdf", ex)]
    assert states(score(c)) == ["CA"]
    r = score(c, Policy(schedule_expired=False))
    assert states(r) == [] and r.excluded[0].reason.startswith("Expired")
    assert status_of(r, 1) == "Expired"


def test_sst_form_in_non_member_state_and_uniform_rejection():
    sst = full(extraction([line("KS", "123"), line("TN", "456"), line("CA", "SR-1")], multistate=True,
                          family="sst_streamlined"))
    r = score([cert(1, "A Resale Certificate Multijurisdiction.pdf", sst)])
    assert row(r, "KS").status == "Valid" and row(r, "TN").status == "Valid"
    assert row(r, "CA").status == "Invalid" and "form_not_accepted" in row(r, "CA").flags
    assert "not an SST member" in row(r, "CA").status_reason
    mtc = full(extraction([line("HI", "GE-1"), line("CO", "2")], multistate=True))
    r = score([cert(2, "B Resale Certificate Multijurisdiction.pdf", mtc)], Policy(uniform_not_accepted="HI"))
    assert row(r, "HI").status == "Invalid" and row(r, "CO").status == "Valid"


def test_fein_and_home_state_settings_by_state():
    ex = full(extraction([line("CA", "SR-1"), line("TX", "95-1", "fein"), line("GA", "95-1", "fein"),
                          line("AL", "78-1", "home_state_number", issuing="FL")], multistate=True))
    c = [cert(1, "A Resale Certificate Multijurisdiction.pdf", ex)]
    assert states(score(c)) == ["AL", "CA", "GA", "TX"]
    assert states(score(c, Policy(fein_exceptions="TX", home_state_exceptions="AL"))) == ["CA", "GA"]
    assert states(score(c, Policy(include_fein=False, fein_exceptions="GA"))) == ["AL", "CA", "GA"]


def test_non_resale_certificates_skip_the_registration_test():
    mfg = full(extraction([line("OH", "", "blank")], cert_type="manufacturing", form="Manufacturing exemption"))
    r = score([cert(1, "A - Exemption Certificate (OH).pdf", mfg.model_copy(update={"exemption_reason": "Used in production"}))])
    assert states(r) == ["OH"] and r.rows[0].basis == "exemption_type" and status_of(r, 1) == "Valid"
    resale = full(extraction([line("OH", "", "blank")]))
    r = score([cert(2, "B - Resale Certificate (OH).pdf", resale)])
    assert states(r) == [] and status_of(r, 2) == "Invalid"
    r = score([cert(3, "C - Exemption Certificate (OH).pdf", mfg)])
    assert "missing_exemption_reason" in codes(r, 3) and status_of(r, 3) == "Pending"


def test_low_confidence_parts_are_flagged_for_the_reviewer():
    fc = FieldConfidence(form="high", purchaser="low", seller="high", registration_numbers="medium",
                         signature="low", expiration="high")
    r = score([cert(1, "A - Resale Certificate (CA).pdf", full(extraction([line("CA", "SR-1")]), field_confidence=fc))])
    msg = next(f.message for c in r.certificates for f in c.flags if f.code == "uncertain_fields")
    assert "purchaser" in msg and "signature" in msg and "registration" not in msg


def test_customer_name_variants_merge_and_reviewer_joins():
    assert customer_key("ACME Supply, Inc.") == customer_key("Acme Supply") == "acme supply"
    assert customer_key("The Widget Co. LLC") == "widget"
    assert customer_key("Acme Supply LLC dba Acme Tools") == "acme supply"
    c = [cert(1, "Acme Supply Inc - Resale Certificate (CA).pdf", full(extraction([line("CA", "SR-1")]))),
         cert(2, "ACME Supply - Resale Certificate (TX).pdf", full(extraction([line("TX", "32")]))),
         cert(3, "Acme West - Resale Certificate (NV).pdf", full(extraction([line("NV", "NV-1")])))]
    r = score(c)
    assert len({x.customer for x in r.rows}) == 2 and len(r.customers()) == 2
    r = score(c, Policy(customer_aliases={"acme west": customer_key("Acme Supply")}))
    assert len({x.customer for x in r.rows}) == 1
    assert r.customers()[0]["states"] == {"CA", "TX", "NV"}


# --- completeness ------------------------------------------------------------

def completeness_of(result, cid=1):
    return next(c for c in result.certificates if c.cert["id"] == cid).completeness


def test_complete_form_reads_valid():
    ex = full(extraction([line("CA", "SR-1")]), property_description="Lighting fixtures for resale")
    r = score([cert(1, "A - Resale Certificate (CA).pdf", ex)])
    assert completeness_of(r) == "Valid" and r.rows[0].completeness == "Valid"
    assert r.rows[0].product_description == "Lighting fixtures for resale"


def test_missing_items_are_listed():
    ex = full(extraction([line("CA", "SR-1")]), seller=Party(name=None, address=None, state=None),
              blank_fields=["Description of property purchased", "Expiration date"])
    r = score([cert(1, "A - Resale Certificate (CA).pdf", ex)])
    assert completeness_of(r) == "Missing: seller name, product description"
    assert "incomplete_form" in codes(r, 1)
    assert status_of(r, 1) == "Pending"            # until a reviewer accepts it


def test_many_missing_items_read_review_for_validity():
    ex = extraction([line("CA", "SR-1")], signed=False, purchaser=None).model_copy(update={
        "seller": Party(name=None, address=None, state=None), "blank_fields": ["Purchaser signature", "Date"]})
    r = score([cert(1, "A - Resale Certificate (CA).pdf", ex)])
    label = completeness_of(r)
    assert label.startswith("Review for validity: missing signature")
    for item in ("buyer name", "buyer address", "seller name"):
        assert item in label
    assert label.count("signature") == 1
    # The threshold is a review policy setting.
    assert completeness_of(score([cert(1, "A - Resale Certificate (CA).pdf", ex)], Policy(review_if_missing=10))
                           ).startswith("Missing:")


def test_filename_only_readings_are_not_checked():
    ex = extraction([line("CA", "SR-1")])
    r = score([{**cert(1, "A - Resale Certificate (CA).pdf", ex), "extractor": "demo"}])
    assert completeness_of(r).startswith("Not checked")


def test_blank_signature_line_reported_by_reader_is_unsigned():
    ex = full(extraction([line("FL", "78-1")], signed=None), blank_fields=["Purchaser signature"])
    r = score([cert(1, "A - Resale Certificate (FL).pdf", ex)])
    assert completeness_of(r) == "Missing: signature" and status_of(r, 1) == "Invalid"
