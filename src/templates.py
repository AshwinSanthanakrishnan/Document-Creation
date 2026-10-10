"""Letter layouts with {{ placeholder }} fields, and the renderer that fills them.

A letter is a list of Blocks. The builder turns the same blocks into both the
.docx and the .pdf, so the two formats always say exactly the same thing.

Unknown placeholders are left in place on purpose: validation then catches them
and the document is rejected rather than sent out with a gap.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping

PLACEHOLDER = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")


@dataclass(frozen=True)
class Block:
    """One piece of a letter.

    kind is one of:
      date, address, subject, salutation, paragraph, heading, placeholder,
      numbered, bullets, exhibits, signature, sign_line
    content is a string, or a list of strings for address/numbered/bullets/exhibits/signature/sign_line.
    A list block may also be a single "{{ name }}" string that expands to a list from the context.
    """

    kind: str
    content: str | list[str]


ENGAGEMENT_LETTER: list[Block] = [
    Block("date", "{{ letter_date }}"),
    Block("address", ["{{ client_name }}", "{{ address_line_1 }}", "{{ address_line_2 }}", "Via email: {{ client_email }}"]),
    Block("subject", "Re: Engagement for Legal Services — {{ case_type }} (Case ID {{ case_id }})"),
    Block("salutation", "Dear {{ client_name }},"),
    Block(
        "paragraph",
        "Thank you for choosing {{ firm_name }}. This letter confirms that our firm will represent you "
        "in your {{ case_type }} matter, which we opened on {{ date_opened }}, and explains the terms of "
        "our engagement. Please read it carefully and let us know if you have any questions.",
    ),
    Block("heading", "Scope of Services"),
    Block("paragraph", "{{ scope_of_services }}"),
    Block(
        "paragraph",
        "This engagement covers only the matter described above. Any appeal, related filing, or separate "
        "immigration benefit would require a new or amended engagement letter.",
    ),
    Block("heading", "Fees and Costs"),
    Block(
        "placeholder",
        "[ATTORNEY TO COMPLETE: legal fees, government filing fees, payment schedule, and retainer terms.]",
    ),
    Block("heading", "Next Steps"),
    Block("paragraph", "{{ next_steps }}"),
    Block(
        "paragraph",
        "To accept this engagement, please sign below and return a copy to our office. If you have "
        "questions at any time, contact {{ attorney_name }} at {{ firm_phone }} and mention Case ID {{ case_id }}.",
    ),
    Block("paragraph", "We appreciate the trust you have placed in us and look forward to working with you."),
    Block("signature", ["Sincerely,", "", "{{ attorney_name }}", "Attorney at Law", "{{ firm_name }}"]),
    Block("heading", "Client Acknowledgment"),
    Block(
        "paragraph",
        "I have read this letter and agree to engage {{ firm_name }} on the terms described above.",
    ),
    Block("sign_line", ["{{ client_name }}"]),
]


DOCUMENT_REQUEST_LETTER: list[Block] = [
    Block("date", "{{ letter_date }}"),
    Block("address", ["{{ client_name }}", "{{ address_line_1 }}", "{{ address_line_2 }}", "Via email: {{ client_email }}"]),
    Block("subject", "Re: Documents Needed for Your {{ case_type }} Case (Case ID {{ case_id }})"),
    Block("salutation", "Dear {{ client_name }},"),
    Block("paragraph", "{{ request_intro }}"),
    Block("paragraph", "To complete your file, please send us the following {{ missing_count_text }}:"),
    Block("numbered", "{{ missing_documents }}"),
    Block(
        "paragraph",
        "Please send these by {{ due_date }}. If you have trouble getting any of them, let us know. "
        "There are often alternatives we can discuss.",
    ),
    Block("heading", "How to Send Your Documents"),
    Block(
        "bullets",
        [
            "Upload clear, complete copies (every page, front and back) through our secure client portal, "
            "or bring them to our office.",
            "Write your Case ID, {{ case_id }}, on everything you send.",
            "Please do not send original documents unless we specifically ask for them.",
            "If a document is not in English, include a certified English translation with it.",
        ],
    ),
    Block("heading", "Documents We Have Already Received"),
    Block("bullets", "{{ received_documents }}"),
    Block(
        "paragraph",
        "Thank you for your help. If you have any questions, please contact {{ attorney_name }} at "
        "{{ firm_phone }} and mention Case ID {{ case_id }}.",
    ),
    Block("signature", ["Warm regards,", "", "{{ attorney_name }}", "Attorney at Law", "{{ firm_name }}"]),
]


# Filing cover letter: generated only when every required document has arrived.
# No AI on purpose: a letter to the government should read the same every time.
G28_FORM = "Form G-28, Notice of Entry of Appearance as Attorney or Accredited Representative"

FILING_COVER_LETTER: list[Block] = [
    Block("date", "{{ letter_date }}"),
    Block("address", ["U.S. Citizenship and Immigration Services"]),
    Block("placeholder", "[ATTORNEY TO CONFIRM: filing address from the current USCIS “Where to File” page.]"),
    Block("subject", "Re: {{ forms_short }} Filing for {{ client_name }} (Our Reference: {{ case_id }})"),
    Block("salutation", "Dear Sir or Madam:"),
    Block(
        "paragraph",
        "On behalf of our client, {{ client_name }}, {{ firm_name }} respectfully submits the enclosed "
        "{{ forms_short }} with supporting evidence. Our Form G-28 is enclosed, and we ask that all notices "
        "and correspondence about this filing also be sent to our office.",
    ),
    Block("heading", "Enclosed Forms"),
    Block("bullets", "{{ enclosed_forms }}"),
    Block("placeholder", "[ATTORNEY TO CONFIRM: filing fee amount and payment method.]"),
    Block("heading", "Exhibit List"),
    Block("paragraph", "The supporting evidence is tabbed and organized as follows:"),
    Block("exhibits", "{{ exhibits }}"),
    Block(
        "paragraph",
        "Please contact {{ attorney_name }} at {{ firm_phone }} with any questions about this filing. "
        "Thank you for your attention to this matter.",
    ),
    Block("signature", ["Respectfully submitted,", "", "{{ attorney_name }}", "Attorney at Law", "{{ firm_name }}"]),
]


def exhibit_label(index: int) -> str:
    """Exhibit letters: 1 -> A, 26 -> Z, 27 -> AA."""
    label = ""
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        label = chr(ord("A") + remainder) + label
    return label


def render_text(template: str, context: Mapping[str, object]) -> str:
    """Replace {{ name }} with context[name]. Unknown names are left untouched."""

    def substitute(match: re.Match) -> str:
        key = match.group(1)
        value = context.get(key)
        if value is None or isinstance(value, (list, tuple)):
            return match.group(0)
        return str(value)

    return PLACEHOLDER.sub(substitute, template)


def render_letter(template: list[Block], context: Mapping[str, object]) -> list[Block]:
    """Fill every block of a letter template from the context."""
    rendered: list[Block] = []
    for block in template:
        if isinstance(block.content, str):
            whole = PLACEHOLDER.fullmatch(block.content.strip())
            value = context.get(whole.group(1)) if whole else None
            if isinstance(value, (list, tuple)):
                rendered.append(Block(block.kind, [str(item) for item in value]))
            else:
                rendered.append(Block(block.kind, render_text(block.content, context)))
        else:
            rendered.append(Block(block.kind, [render_text(line, context) for line in block.content]))
    return rendered


def letter_text(blocks: list[Block]) -> str:
    """Flatten a rendered letter into plain text (used for validation)."""
    parts: list[str] = []
    for block in blocks:
        parts.extend([block.content] if isinstance(block.content, str) else block.content)
    return "\n".join(parts)
