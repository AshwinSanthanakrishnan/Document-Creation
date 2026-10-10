"""Per-client metadata.json files and the run-level report (run_report.md, .docx and .pdf)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.ai_drafter import DraftResult
from src.config import PROJECT_ROOT, SAFETY_HEADER
from src.report_builder import write_report_docx, write_report_pdf

DOCUMENT_LABELS = {
    "engagement_letter": "Engagement letter",
    "document_request": "Document request",
    "filing_cover_letter": "Filing cover letter",
}
SECTION_LABELS = {
    "scope_of_services": "Scope of services",
    "next_steps": "Next steps",
    "request_intro": "Document request intro",
}


@dataclass
class GeneratedFile:
    document: str  # e.g. "engagement_letter"
    path: Path
    created_at: datetime


@dataclass
class ClientResult:
    case_id: str
    client_name: str = "(unparsed record)"
    case_type: str = "(unknown)"
    status: str = "pending"  # "success" or "failed"
    files: list[GeneratedFile] = field(default_factory=list)
    required_count: int = 0
    missing_documents: list[str] = field(default_factory=list)
    unrecognized_documents: list[str] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # {"document": ..., "reason": ...}
    ai_sections: list[DraftResult] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0

    @property
    def documents_generated(self) -> list[str]:
        return list(dict.fromkeys(f.document for f in self.files))

    @property
    def fallbacks(self) -> list[DraftResult]:
        return [s for s in self.ai_sections if s.used_fallback]


@dataclass
class RunInfo:
    started_at: datetime
    finished_at: datetime
    scope: str
    ai_mode: str
    log_file: Path

    @property
    def duration_seconds(self) -> float:
        return (self.finished_at - self.started_at).total_seconds()


def display_path(path: Path) -> str:
    """Path relative to the project root when possible (for logs and reports)."""
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_metadata(result: ClientResult, out_dir: Path) -> Path:
    """Write outputs/<case_id>/metadata.json describing what was generated and how."""
    metadata = {
        "case_id": result.case_id,
        "client_name": result.client_name,
        "case_type": result.case_type,
        "status": result.status,
        "error": result.error,
        "generated_at": max((f.created_at for f in result.files), default=datetime.now().astimezone()).isoformat(timespec="seconds"),
        "safety_header": SAFETY_HEADER,
        "files": [
            {
                "document": f.document,
                "file": f.path.name,
                "format": f.path.suffix.lstrip("."),
                "created_at": f.created_at.isoformat(timespec="seconds"),
                "size_bytes": f.path.stat().st_size,
                "sha256": _sha256(f.path),
                "validated": True,  # build_letter deletes any file that fails validation
            }
            for f in result.files
            if f.path.exists()
        ],
        "skipped_documents": result.skipped,
        "document_status": {
            "required": result.required_count,
            "missing": result.missing_documents,
            "received_but_not_required": result.unrecognized_documents,
        },
        "ai_sections": [s.to_dict() for s in result.ai_sections],
    }
    path = out_dir / "metadata.json"
    path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


@dataclass
class ReportPart:
    """One piece of the run report, rendered to Markdown, .docx and .pdf alike.

    kind: title, note, kv, heading, table, bullets, paragraph
    Text may use Markdown `code` and **bold**; the .docx/.pdf writers handle both.
    """

    kind: str
    content: str | list = ""
    header: list[str] | None = None  # table column names


def build_run_report(results: list[ClientResult], info: RunInfo) -> list[ReportPart]:
    """Lay out the summary for firm staff once, for every output format."""
    failed = [r for r in results if r.status != "success"]
    done = [r for r in results if r.status == "success"]
    ready = [r for r in done if not r.missing_documents]
    waiting = [r for r in done if r.missing_documents]
    letters = sum(len(r.documents_generated) for r in results)

    parts = [
        ReportPart("title", "Client Letters Summary"),
        ReportPart("note", f"**{SAFETY_HEADER}** Every letter listed here is a draft. An attorney must review it "
                           "before it is sent."),
        ReportPart("kv", [
            ["Prepared on", f"{info.started_at:%B} {info.started_at.day}, {info.started_at.year}"],
            ["Clients", str(len(results))],
            ["Letters prepared", f"{letters} (each saved as a Word file and a PDF)"],
            ["Ready to file", f"{len(ready)} client(s): all required documents received"],
            ["Waiting on documents", f"{len(waiting)} client(s): a document request letter is ready to send"],
        ]),
        ReportPart("heading", "Clients"),
    ]
    rows = []
    for r in results:
        letters_made = ", ".join(DOCUMENT_LABELS.get(d, d) for d in r.documents_generated) or "None"
        received = f"{r.required_count - len(r.missing_documents)} of {r.required_count}" if r.required_count else "n/a"
        if r.status != "success":
            next_step = "Letters could not be prepared; see below"
        elif r.missing_documents:
            next_step = f"Send the document request ({len(r.missing_documents)} still needed)"
        else:
            next_step = "Ready to file: review the filing cover letter"
        rows.append([f"`{r.case_id}`", r.client_name, r.case_type, letters_made, received, next_step])
    parts.append(ReportPart("table", rows, header=[
        "Case ID", "Client", "Case type", "Letters prepared", "Documents received", "Next step"]))

    parts.append(ReportPart("heading", "Documents still needed from clients"))
    if not waiting:
        parts.append(ReportPart("paragraph", "None. Every client has sent all required documents."))
    for r in waiting:
        parts.append(ReportPart("paragraph", f"**`{r.case_id}` {r.client_name}** ({r.case_type}): "
                                             f"{len(r.missing_documents)} still needed"))
        parts.append(ReportPart("bullets", list(r.missing_documents)))
    unrecognized = [r for r in results if r.unrecognized_documents]
    if unrecognized:
        parts.append(ReportPart("paragraph", "Extra documents received that are not on the checklist (kept on file):"))
        parts.append(ReportPart("bullets", [f"`{r.case_id}` {r.client_name}: {', '.join(r.unrecognized_documents)}"
                                            for r in unrecognized]))

    if failed:
        parts.append(ReportPart("heading", "Clients that need attention"))
        parts.append(ReportPart("paragraph", "Letters could not be prepared for these clients. Check their case details "
                                             "and run them again."))
        parts.append(ReportPart("table", [[f"`{r.case_id}`", r.client_name, r.error or ""] for r in failed],
                                header=["Case ID", "Client", "Problem"]))

    parts += [
        ReportPart("heading", "Before sending any letter"),
        ReportPart("bullets", [
            "Read each letter and confirm the client's name, address and case details.",
            "Fill in the highlighted items: fees, filing fees and the filing address.",
            "Remove the DRAFT banner only after the attorney has approved the letter.",
            "Letters are saved in the outputs folder, in one folder per case ID.",
        ]),
    ]
    return parts


def _md_row(cells: list[str]) -> str:
    return "| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |"


def render_markdown(parts: list[ReportPart]) -> str:
    out = []
    for part in parts:
        if part.kind == "title":
            out.append(f"# {part.content}")
        elif part.kind == "heading":
            out.append(f"## {part.content}")
        elif part.kind == "note":
            out.append(f"> {part.content}")
        elif part.kind == "paragraph":
            out.append(part.content)
        elif part.kind == "bullets":
            out.append("\n".join(f"- {item}" for item in part.content))
        elif part.kind in ("kv", "table"):
            header = part.header or ["", ""]
            lines = [_md_row(header), "|" + "---|" * len(header)]
            out.append("\n".join(lines + [_md_row(row) for row in part.content]))
        else:
            raise ValueError(f"Unknown report part '{part.kind}'")
    return "\n\n".join(out) + "\n"


def write_run_report(results: list[ClientResult], info: RunInfo, out_dir: Path) -> list[Path]:
    """Write run_report.md, run_report.docx and run_report.pdf; return their paths."""
    parts = build_run_report(results, info)
    md_path = out_dir / "run_report.md"
    md_path.write_text(render_markdown(parts), encoding="utf-8")
    return [md_path, write_report_docx(parts, out_dir / "run_report.docx"), write_report_pdf(parts, out_dir / "run_report.pdf")]
