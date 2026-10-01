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
