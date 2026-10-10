"""Per-client metadata.json files and the run-level run_report.md."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from src.ai_drafter import DraftResult
from src.config import PROJECT_ROOT, SAFETY_HEADER

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


def write_run_report(results: list[ClientResult], info: RunInfo, path: Path) -> Path:
    """Write a Markdown summary of the whole run."""
    succeeded = [r for r in results if r.status == "success"]
    failed = [r for r in results if r.status != "success"]
    letters = sum(len(r.documents_generated) for r in results)
    files = sum(len(r.files) for r in results)
    ai_total = sum(len(r.ai_sections) for r in results)
    ai_fallback = sum(len(r.fallbacks) for r in results)

    lines = [
        "# Run Report: Legal Document Generator",
        "",
        f"> **{SAFETY_HEADER}** Every generated document is a draft that a licensed attorney must review. "
        "All client data in this demo is fictional.",
        "",
        "| | |",
        "|---|---|",
        f"| Run started | {info.started_at.strftime('%Y-%m-%d %H:%M:%S %Z')} |",
        f"| Total time | {info.duration_seconds:.1f} seconds |",
        f"| Scope | {info.scope} |",
        f"| AI drafting | {info.ai_mode} |",
        f"| Clients processed | {len(results)} ({len(succeeded)} succeeded, {len(failed)} failed) |",
        f"| Documents generated | {letters} letters ({files} files: .docx + .pdf) |",
        f"| AI sections | {ai_total - ai_fallback} drafted by Gemini, {ai_fallback} used fallback text |",
        f"| Log file | `{display_path(info.log_file)}` |",
        "",
        "## Clients",
        "",
        "| Case ID | Client | Case type | Documents generated | Missing docs | AI fallbacks | Status | Time |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        generated = ", ".join(DOCUMENT_LABELS.get(d, d) for d in r.documents_generated) or "none"
        missing = f"{len(r.missing_documents)} of {r.required_count}" if r.required_count else "n/a"
        status = "✅ Success" if r.status == "success" else "❌ Failed"
        fallback = f"⚠️ {len(r.fallbacks)}" if r.fallbacks else "0"
        lines.append(
            f"| `{r.case_id}` | {r.client_name} | {r.case_type} | {generated} | {missing} | {fallback} | {status} | {r.duration_seconds:.1f}s |"
        )

    lines += ["", "## Missing documents", ""]
    with_missing = [r for r in results if r.missing_documents]
    if not with_missing:
        lines.append("No outstanding documents.")
    for r in with_missing:
        lines.append(f"**`{r.case_id}` {r.client_name}** ({r.case_type}): {len(r.missing_documents)} missing")
        lines += [f"- {doc}" for doc in r.missing_documents]
        lines.append("")
    unrecognized = [r for r in results if r.unrecognized_documents]
    if unrecognized:
        if lines[-1]:
            lines.append("")
        lines += ["Received but not on the requirements list (kept on file, not counted):", ""]
        lines += [f"- `{r.case_id}`: {', '.join(r.unrecognized_documents)}" for r in unrecognized]

    lines += ["", "## Skipped documents", ""]
    skipped = [(r, s) for r in results for s in r.skipped]
    if not skipped:
        lines.append("None.")
    for r, s in skipped:
        lines.append(f"- `{r.case_id}` {DOCUMENT_LABELS.get(s['document'], s['document'])}: {s['reason']}")

    lines += ["", "## ⚠️ AI fallbacks: need attorney attention", ""]
    fallbacks = [(r, s) for r in results for s in r.fallbacks]
    if not fallbacks:
        lines.append("None. Every AI section was drafted by Gemini.")
    else:
        lines += [
            "These sections use neutral template text instead of a tailored Gemini draft. "
            "Review and personalize them before sending.",
            "",
            "| Case ID | Section | Attempts | Reason |",
            "|---|---|---|---|",
        ]
        lines += [
            f"| `{r.case_id}` | {SECTION_LABELS.get(s.section, s.section)} | {s.attempts} | {s.error or 'unknown'} |"
            for r, s in fallbacks
        ]

    lines += ["", "## Failures", ""]
    if not failed:
        lines.append("None.")
    else:
        lines += ["| Case ID | Client | Error |", "|---|---|---|"]
        lines += [f"| `{r.case_id}` | {r.client_name} | {r.error} |" for r in failed]

    lines += [
        "",
        "## Validation",
        "",
        f"- {files} files were re-opened after writing and checked for unfilled `{{{{ }}}}` placeholders "
        "and the safety banner on every page. All of them passed; any file that fails is deleted and its client "
        "is marked failed.",
        "- Client names, addresses, case IDs, dates and document lists come straight from the data files. "
        "Only the prose sections listed above come from AI.",
        "",
    ]

    path.write_text("\n".join(lines), encoding="utf-8")
    return path
