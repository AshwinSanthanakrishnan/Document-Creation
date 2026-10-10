"""Tests for placeholder rendering/validation and the safety banner on finished files."""

from datetime import date

import pytest
from reportlab.pdfgen import canvas

from main import format_date
from src.ai_drafter import GeminiDrafter
from src.config import SAFETY_HEADER
from src.document_builder import build_letter, write_docx, write_pdf
from src.templates import ENGAGEMENT_LETTER, FILING_COVER_LETTER, G28_FORM, Block, exhibit_label, render_letter, render_text
from src.validation import (
    MissingSafetyHeaderError,
    UnfilledPlaceholderError,
    assert_no_placeholders,
    find_unfilled_placeholders,
    read_docx_text,
    read_pdf_pages,
    validate_docx,
    validate_pdf,
)


# ---- finding placeholders in text

@pytest.mark.parametrize(
    "text, expected",
    [
        ("Dear {{ client_name }},", ["{{ client_name }}"]),
        ("Case {{case_id}} opened", ["{{case_id}}"]),
        ("Broken {{ template", ["{{"]),
        ("Broken template }}", ["}}"]),
        ("Wrapped by PDF extraction {{ client_\nname }}", ["{{ client_ name }}"]),
        ("{{ a }} and {{ b }}", ["{{ a }}", "{{ b }}"]),
    ],
)
def test_finds_unfilled_placeholders(text, expected):
    assert find_unfilled_placeholders(text) == expected


def test_clean_text_passes():
    assert find_unfilled_placeholders("Dear Priya Raman, see the {attached} schedule.") == []
    assert_no_placeholders("A finished letter with no gaps.", "test")


def test_assert_no_placeholders_names_what_is_missing():
    with pytest.raises(UnfilledPlaceholderError, match=r"2 unfilled placeholder\(s\): \{\{ fee \}\}, \{\{ due \}\}"):
        assert_no_placeholders("Pay {{ fee }} by {{ due }}.", "letter.docx")


# ---- rendering

def test_render_fills_known_fields_and_leaves_unknown_ones_for_validation():
    text = render_text("Dear {{ client_name }}, your case {{ case_idd }}", {"client_name": "Wei Chen", "case_id": "X-1"})
    assert text == "Dear Wei Chen, your case {{ case_idd }}"
    assert find_unfilled_placeholders(text) == ["{{ case_idd }}"]


def test_list_placeholder_expands_to_items():
    blocks = render_letter([Block("numbered", "{{ docs }}")], {"docs": ["Passport", "Birth certificate"]})
    assert blocks[0].content == ["Passport", "Birth certificate"]


def test_full_template_renders_with_no_gaps():
    blocks = render_letter(ENGAGEMENT_LETTER, _engagement_context())
    for block in blocks:
        lines = [block.content] if isinstance(block.content, str) else block.content
        for line in lines:
            assert find_unfilled_placeholders(line) == [], line


@pytest.mark.parametrize("index, label", [(1, "A"), (6, "F"), (26, "Z"), (27, "AA"), (28, "AB")])
def test_exhibit_labels(index, label):
    assert exhibit_label(index) == label


# ---- finished files

def test_filing_cover_letter_lists_every_exhibit_in_both_formats(tmp_path):
    exhibits = ["Permanent resident card (front and back)", "Passport (biographic page)"]
    context = {**_engagement_context(), "forms_short": "Form N-400",
               "enclosed_forms": [G28_FORM, "Form N-400, Application for Naturalization"], "exhibits": exhibits}
    docx_path, pdf_path = build_letter(render_letter(FILING_COVER_LETTER, context), tmp_path, "filing_cover_letter",
                                       title="t", case_id="TEST-1")

    body, _ = read_docx_text(docx_path)
    pdf_text = " ".join(" ".join(read_pdf_pages(pdf_path)).split())
    for label, doc in zip("AB", exhibits):
        assert f"Exhibit {label}\t{doc}" in body
        assert f"Exhibit {label} {doc}" in pdf_text
    assert "Form G-28" in pdf_text and "Form N-400" in pdf_text



def test_build_refuses_unfilled_placeholder_and_writes_nothing(tmp_path):
    blocks = render_letter(ENGAGEMENT_LETTER, {**_engagement_context(), "attorney_name": None})
    with pytest.raises(UnfilledPlaceholderError, match="attorney_name"):
        build_letter(blocks, tmp_path, "engagement_letter", title="t", case_id="TEST-1")
    assert list(tmp_path.iterdir()) == []


def test_validators_catch_placeholders_inside_finished_files(tmp_path):
    """Bypass the pre-write check and confirm the file-level check still catches the gap."""
    blocks = [Block("paragraph", "Dear {{ client_name }},")]
    docx_path, pdf_path = tmp_path / "bad.docx", tmp_path / "bad.pdf"
    write_docx(blocks, docx_path, title="t", case_id="TEST-1")
    write_pdf(blocks, pdf_path, title="t", case_id="TEST-1")

    with pytest.raises(UnfilledPlaceholderError):
        validate_docx(docx_path)
    with pytest.raises(UnfilledPlaceholderError):
        validate_pdf(pdf_path)


def test_generated_letter_has_safety_header_on_every_page(tmp_path):
    blocks = render_letter(ENGAGEMENT_LETTER, _engagement_context())
    docx_path, pdf_path = build_letter(blocks, tmp_path, "engagement_letter", title="t", case_id="TEST-1")

    _, headers = read_docx_text(docx_path)
    assert headers and all(SAFETY_HEADER in h for h in headers)

    pages = read_pdf_pages(pdf_path)
    assert len(pages) >= 2  # the engagement letter runs onto a second page
    assert all(SAFETY_HEADER in " ".join(p.split()) for p in pages)


def test_pdf_without_safety_header_is_rejected(tmp_path):
    path = tmp_path / "unmarked.pdf"
    c = canvas.Canvas(str(path))
    c.drawString(72, 720, "A letter with no draft banner.")
    c.save()
    with pytest.raises(MissingSafetyHeaderError):
        validate_pdf(path)


def _engagement_context() -> dict:
    drafter = GeminiDrafter(api_key=None, model="test", offline=True)
    return {
        "firm_name": "Sample Immigration Firm",
        "firm_phone": "(512) 555-0100",
        "letter_date": format_date(date(2026, 10, 8)),
        "client_name": "Test Person",
        "address_line_1": "1 Test St",
        "address_line_2": "Austin, TX 78701",
        "client_email": "test@example.com",
        "case_type": "Asylum",
        "case_id": "TEST-1",
        "date_opened": format_date(date(2026, 9, 1)),
        "attorney_name": "A. Lawyer",
        "scope_of_services": drafter.draft("scope_of_services", case_type="Asylum").text,
        "next_steps": drafter.draft("next_steps", case_type="Asylum").text,
    }
