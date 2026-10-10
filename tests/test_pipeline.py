"""End-to-end run on temporary data: one bad client must not stop the others."""

import json

import main
from src import config

GOOD = {
    "case_id": "TEST-GOOD",
    "full_name": "Good Client",
    "address": {"street": "1 Test St", "city": "Austin", "state": "TX", "postal_code": "78701"},
    "email": "good@example.com",
    "phone": "(512) 555-0198",
    "country_of_citizenship": "Canada",
    "case_type": "Naturalization",
    "assigned_attorney": "A. Lawyer",
    "date_opened": "2026-01-15",
    "documents_received": ["Passport (biographic page)"],
}
BAD = {**GOOD, "case_id": "TEST-BAD", "full_name": "Bad Client", "case_type": "Tourist Visa"}


def test_failed_client_is_reported_and_the_run_continues(tmp_path, monkeypatch):
    clients_file = tmp_path / "clients.json"
    clients_file.write_text(json.dumps({"clients": [BAD, GOOD]}))
    monkeypatch.setattr(config, "CLIENTS_FILE", clients_file)
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")

    exit_code = main.main(["--offline"])

    assert exit_code == 1  # any failure makes the run exit non-zero
    good_dir = tmp_path / "outputs" / "TEST-GOOD"
    assert {p.name for p in good_dir.iterdir()} == {
        "engagement_letter.docx", "engagement_letter.pdf",
        "document_request.docx", "document_request.pdf",
        "metadata.json",
    }
    metadata = json.loads((good_dir / "metadata.json").read_text())
    assert metadata["status"] == "success"
    assert all(s["source"] == "fallback" for s in metadata["ai_sections"])

    report = (tmp_path / "outputs" / "run_report.md").read_text()
    assert "1 succeeded, 1 failed" in report
    assert "Unknown case type 'Tourist Visa'" in report
    assert not (tmp_path / "outputs" / "TEST-BAD").exists()
    assert list((tmp_path / "logs").glob("run_*.log"))


def test_single_client_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path / "outputs")
    monkeypatch.setattr(config, "LOG_DIR", tmp_path / "logs")

    assert main.main(["--offline", "--client", "IMM-2026-0104"]) == 0
    assert [p.name for p in (tmp_path / "outputs").iterdir() if p.is_dir()] == ["IMM-2026-0104"]
    # Naturalization client has every document: a filing cover letter instead of a request letter.
    client_dir = tmp_path / "outputs" / "IMM-2026-0104"
    assert not (client_dir / "document_request.pdf").exists()
    assert (client_dir / "filing_cover_letter.docx").exists() and (client_dir / "filing_cover_letter.pdf").exists()

    assert main.main(["--offline", "--client", "NOPE-0000"]) == 2
