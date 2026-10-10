"""Tests for the missing-documents logic and client data validation."""

import pytest

from src.config import CLIENTS_FILE, REQUIREMENTS_FILE
from src.data_loader import (
    DataError,
    compute_document_status,
    load_client_records,
    load_filing_forms,
    load_requirements,
    parse_client,
    requirements_for,
)

REQUIRED = ["Passport", "Birth certificate", "Marriage certificate", "Joint financial records"]


def test_nothing_received_means_everything_missing():
    status = compute_document_status(REQUIRED, [])
    assert status.missing == REQUIRED
    assert status.received == []


def test_partial_receipt_lists_missing_in_requirement_order():
    status = compute_document_status(REQUIRED, ["Joint financial records", "Passport"])
    assert status.missing == ["Birth certificate", "Marriage certificate"]
    assert status.received == ["Passport", "Joint financial records"]


def test_everything_received_means_nothing_missing():
    status = compute_document_status(REQUIRED, list(reversed(REQUIRED)))
    assert status.missing == []
    assert status.received == REQUIRED


def test_matching_ignores_case_and_extra_whitespace():
    status = compute_document_status(REQUIRED, ["  passport ", "BIRTH   CERTIFICATE"])
    assert status.missing == ["Marriage certificate", "Joint financial records"]


def test_unrequired_documents_are_reported_but_not_counted():
    status = compute_document_status(REQUIRED, ["Passport", "Recent pay stubs", "recent pay stubs"])
    assert status.unrecognized == ["Recent pay stubs"]
    assert "Recent pay stubs" not in status.received
    assert len(status.missing) == 3


def test_duplicates_do_not_inflate_counts():
    status = compute_document_status(REQUIRED + ["passport"], ["Passport", "Passport"])
    assert status.required == REQUIRED
    assert status.received == ["Passport"]
    assert len(status.missing) == 3


def test_unknown_case_type_fails_clearly():
    with pytest.raises(DataError, match="Unknown case type 'Tourist Visa'"):
        requirements_for("Tourist Visa", {"Asylum": ["Passport"]})


def test_sample_data_covers_both_letter_paths():
    """The demo data should exercise both 'request letter' and 'nothing missing, skip' paths."""
    requirements = load_requirements(REQUIREMENTS_FILE)
    clients = [parse_client(r) for r in load_client_records(CLIENTS_FILE)]
    missing = {c.case_type: compute_document_status(requirements_for(c.case_type, requirements), c.documents_received).missing
               for c in clients}

    assert set(missing) == {"H-1B Work Visa", "Family-Based Green Card", "Asylum", "Naturalization"}
    assert missing["Naturalization"] == []
    assert all(missing[t] for t in ("H-1B Work Visa", "Family-Based Green Card", "Asylum"))


def _record(**overrides):
    record = {
        "case_id": "TEST-1",
        "full_name": "Test Person",
        "address": {"street": "1 Test St", "city": "Austin", "state": "TX", "postal_code": "78701"},
        "email": "test@example.com",
        "phone": "(512) 555-0199",
        "country_of_citizenship": "Canada",
        "case_type": "Asylum",
        "assigned_attorney": "A. Lawyer",
        "date_opened": "2026-01-15",
        "documents_received": [],
    }
    record.update(overrides)
    return record


def test_empty_documents_received_is_valid():
    assert parse_client(_record()).documents_received == ()


def test_missing_field_is_rejected():
    record = _record()
    del record["email"]
    with pytest.raises(DataError, match="email"):
        parse_client(record)


@pytest.mark.parametrize("case_id", ["../escape", "a/b", " ", "IMM 01"])
def test_unsafe_case_id_is_rejected(case_id):
    with pytest.raises(DataError):
        parse_client(_record(case_id=case_id))


def test_bad_date_is_rejected():
    with pytest.raises(DataError, match="YYYY-MM-DD"):
        parse_client(_record(date_opened="15/01/2026"))


def test_every_case_type_lists_its_filing_forms():
    forms = load_filing_forms(REQUIREMENTS_FILE)
    assert set(forms) == set(load_requirements(REQUIREMENTS_FILE))
    assert forms["Naturalization"] == ["Form N-400, Application for Naturalization"]


def test_missing_filing_forms_is_rejected(tmp_path):
    path = tmp_path / "reqs.json"
    path.write_text('{"case_types": {"Asylum": {"required_documents": ["Passport"]}}}')
    with pytest.raises(DataError, match="filing_forms"):
        load_filing_forms(path)
