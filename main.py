"""Generate engagement letters and document request letters for every client.

Usage:
    python main.py                       # all clients
    python main.py --client IMM-2026-0101
    python main.py --offline             # skip Gemini; use (flagged) fallback text
"""

from __future__ import annotations

import argparse
import logging
import os
import shutil
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

from src import config
from src.ai_drafter import GeminiDrafter
from src.data_loader import (
    DataError,
    compute_document_status,
    load_client_records,
    load_filing_forms,
    load_requirements,
    parse_client,
    requirements_for,
)
from src.document_builder import build_letter
from src.reporter import ClientResult, GeneratedFile, RunInfo, display_path, write_metadata, write_run_report
from src.templates import (
    DOCUMENT_REQUEST_LETTER,
    ENGAGEMENT_LETTER,
    FILING_COVER_LETTER,
    G28_FORM,
    render_letter,
)

log = logging.getLogger("generator")


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate draft legal letters (.docx + .pdf) from client case data.")
    parser.add_argument("--client", metavar="CASE_ID", help="generate documents for this case ID only")
    parser.add_argument("--offline", action="store_true", help="do not call Gemini; use flagged fallback text")
    return parser.parse_args(argv)


def setup_logging(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"

    console = logging.StreamHandler(sys.stdout)
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers[:] = [console, file_handler]
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    return log_file


def format_date(value: date) -> str:
    return f"{value:%B} {value.day}, {value.year}"


def process_client(
    record: dict,
    requirements: dict[str, list[str]],
    filing_forms: dict[str, list[str]],
    drafter: GeminiDrafter,
    output_root: Path,
    letter_date: date,
) -> ClientResult:
    """Generate every document for one client. Never raises: failures are captured in the result."""
    started = time.perf_counter()
    result = ClientResult(case_id=str(record.get("case_id") or "(missing case_id)"))
    out_dir: Path | None = None

    try:
        client = parse_client(record)
        result.client_name, result.case_type = client.full_name, client.case_type

        required = requirements_for(client.case_type, requirements)
        status = compute_document_status(required, client.documents_received)
        result.required_count = len(status.required)
        result.missing_documents = status.missing
        result.unrecognized_documents = status.unrecognized
        log.info("    %s: %d of %d required documents received, %d missing",
                 client.case_type, len(status.received), len(status.required), len(status.missing))
        if status.unrecognized:
            log.info("    Received but not on requirements list (kept on file): %s", ", ".join(status.unrecognized))

        # Start from a clean folder so files from earlier runs can't linger.
        out_dir = output_root / client.case_id
        if out_dir.exists():
            shutil.rmtree(out_dir)
        out_dir.mkdir(parents=True)

        context = {
            "firm_name": config.FIRM_NAME,
            "firm_phone": config.FIRM_PHONE.replace(" ", " "),  # keep the number on one line
            "letter_date": format_date(letter_date),
            "client_name": client.full_name,
            "address_line_1": client.address.lines()[0],
            "address_line_2": client.address.lines()[1],
            "client_email": client.email,
            "case_type": client.case_type,
            "case_id": client.case_id,
            "date_opened": format_date(client.date_opened),
            "attorney_name": client.assigned_attorney,
        }

        # 1) Engagement letter: always generated.
        log.info("    Drafting engagement letter")
        scope = drafter.draft("scope_of_services", case_type=client.case_type)
        next_steps = drafter.draft("next_steps", case_type=client.case_type)
        result.ai_sections += [scope, next_steps]
        blocks = render_letter(ENGAGEMENT_LETTER, {**context, "scope_of_services": scope.text, "next_steps": next_steps.text})
        paths = build_letter(blocks, out_dir, "engagement_letter",
                             title=f"Engagement Letter: {client.case_type} ({client.case_id})", case_id=client.case_id)
        result.files += [GeneratedFile("engagement_letter", p, datetime.now().astimezone()) for p in paths]

        # 2) Then exactly one of: a request letter to the client (something missing)
        #    or a filing cover letter for the attorney (everything received).
        if not status.missing:
            reason = f"all {len(status.required)} required documents for {client.case_type} have been received"
            result.skipped.append({"document": "document_request", "reason": reason})
            log.info("    No document request needed: %s", reason)

            log.info("    Preparing filing cover letter with %d exhibits", len(status.received))
            forms = filing_forms.get(client.case_type)
            if not forms:
                raise DataError(f"No filing_forms listed for case type '{client.case_type}'")
            blocks = render_letter(FILING_COVER_LETTER, {
                **context,
                "forms_short": " and ".join(f.split(",")[0] for f in forms),  # "Form I-130 and Form I-485"
                "enclosed_forms": [G28_FORM, *forms],
                "exhibits": status.received,
            })
            paths = build_letter(blocks, out_dir, "filing_cover_letter",
                                 title=f"Filing Cover Letter: {client.case_type} ({client.case_id})", case_id=client.case_id)
            result.files += [GeneratedFile("filing_cover_letter", p, datetime.now().astimezone()) for p in paths]
        else:
            reason = f"{len(status.missing)} required document(s) still missing; requested from the client instead"
            result.skipped.append({"document": "filing_cover_letter", "reason": reason})

            log.info("    Drafting document request letter (%d missing)", len(status.missing))
            n = len(status.missing)
            intro = drafter.draft("request_intro", case_type=client.case_type, missing_count=n)
            result.ai_sections.append(intro)
            blocks = render_letter(DOCUMENT_REQUEST_LETTER, {
                **context,
                "request_intro": intro.text,
                "missing_count_text": f"{n} document" if n == 1 else f"{n} documents",
                "missing_documents": status.missing,
                "received_documents": status.received or ["None yet."],
                "due_date": format_date(letter_date + timedelta(days=config.DOCUMENT_REQUEST_DUE_DAYS)),
            })
            paths = build_letter(blocks, out_dir, "document_request",
                                 title=f"Document Request: {client.case_type} ({client.case_id})", case_id=client.case_id)
            result.files += [GeneratedFile("document_request", p, datetime.now().astimezone()) for p in paths]

        result.status = "success"
    except Exception as exc:
        result.status = "failed"
        result.error = f"{type(exc).__name__}: {exc}"
        log.error("    FAILED: %s", result.error)
        if not isinstance(exc, DataError):
            log.debug("Traceback for %s", result.case_id, exc_info=True)
    finally:
        result.duration_seconds = time.perf_counter() - started
        if out_dir is not None and out_dir.exists():
            write_metadata(result, out_dir)

    if result.fallbacks:
        log.warning("    %d AI section(s) used fallback text; flagged in the run report", len(result.fallbacks))
    return result


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    load_dotenv(config.PROJECT_ROOT / ".env")
    log_file = setup_logging(config.LOG_DIR)
    started_at = datetime.now().astimezone()

    log.info("Legal Document Generator: run started")
    try:
        records = load_client_records(config.CLIENTS_FILE)
        requirements = load_requirements(config.REQUIREMENTS_FILE)
        filing_forms = load_filing_forms(config.REQUIREMENTS_FILE)
    except DataError as exc:
        log.error("Cannot start: %s", exc)
        return 2

    scope = "All clients"
    if args.client:
        records = [r for r in records if isinstance(r, dict) and r.get("case_id") == args.client]
        if not records:
            log.error("No client with case_id '%s' in %s", args.client, config.CLIENTS_FILE.name)
            return 2
        scope = f"Single client (`--client {args.client}`)"

    model = os.getenv("GEMINI_MODEL") or config.DEFAULT_GEMINI_MODEL
    drafter = GeminiDrafter(
        api_key=os.getenv("GEMINI_API_KEY"),
        model=model,
        offline=args.offline,
        retry_delay=config.GEMINI_RETRY_DELAY_SECONDS,
        timeout_seconds=config.GEMINI_TIMEOUT_SECONDS,
    )
    if drafter.enabled:
        ai_mode = f"Gemini (`{model}`)"
        log.info("AI drafting: Gemini model %s", model)
    else:
        ai_mode = f"Disabled ({drafter.disabled_reason}). All AI sections use fallback text"
        log.warning("AI drafting disabled (%s). Every AI section will use fallback text and be flagged.",
                    drafter.disabled_reason)

    config.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    letter_date = date.today()
    results: list[ClientResult] = []
    for i, record in enumerate(records, 1):
        case_id = record.get("case_id") if isinstance(record, dict) else None
        log.info("[%d/%d] %s", i, len(records), case_id or "(record without case_id)")
        results.append(process_client(record if isinstance(record, dict) else {}, requirements, filing_forms, drafter,
                                      config.OUTPUT_DIR, letter_date))

    if ai_mode.startswith("Gemini") and not drafter.enabled:
        ai_mode += f", then disabled mid-run: {drafter.disabled_reason}"

    info = RunInfo(started_at, datetime.now().astimezone(), scope, ai_mode, log_file)
    report = write_run_report(results, info, config.OUTPUT_DIR / "run_report.md")

    failed = [r for r in results if r.status != "success"]
    letters = sum(len(r.documents_generated) for r in results)
    fallbacks = sum(len(r.fallbacks) for r in results)
    log.info("Done in %.1fs: %d client(s), %d letter(s), %d failure(s), %d AI fallback(s)",
             info.duration_seconds, len(results), letters, len(failed), fallbacks)
    log.info("Run report: %s", display_path(report))
    log.info("Log file:   %s", display_path(log_file))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
