"""Load and validate client and case-requirement data, and work out which documents are missing."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REQUIRED_CLIENT_FIELDS = (
    "case_id",
    "full_name",
    "address",
    "email",
    "phone",
    "country_of_citizenship",
    "case_type",
    "assigned_attorney",
    "date_opened",
    "documents_received",
)
REQUIRED_ADDRESS_FIELDS = ("street", "city", "state", "postal_code")

# Case IDs become folder names, so keep them to a safe character set.
CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class DataError(Exception):
    """Input data is missing, malformed, or inconsistent."""


@dataclass(frozen=True)
class Address:
    street: str
    city: str
    state: str
    postal_code: str

    def lines(self) -> list[str]:
        return [self.street, f"{self.city}, {self.state} {self.postal_code}"]


@dataclass(frozen=True)
class Client:
    case_id: str
    full_name: str
    address: Address
    email: str
    phone: str
    country_of_citizenship: str
    case_type: str
    assigned_attorney: str
    date_opened: date
    documents_received: tuple[str, ...]


@dataclass(frozen=True)
class DocumentStatus:
    """Result of comparing what a case needs against what the client has sent."""

    required: list[str]
    received: list[str]  # required documents that have arrived, in requirement order
    missing: list[str]  # required documents still outstanding, in requirement order
    unrecognized: list[str]  # documents received that are not on the requirements list


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DataError(f"Data file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DataError(f"Invalid JSON in {path.name}: {exc}") from exc


def load_client_records(path: Path) -> list[dict[str, Any]]:
    """Return the raw client records. Each record is validated later by parse_client,
    so one bad record fails only that client, not the whole run."""
    data = _read_json(path)
    records = data.get("clients") if isinstance(data, dict) else data
    if not isinstance(records, list) or not records:
        raise DataError(f"{path.name} must contain a non-empty 'clients' list")
    return records


def load_requirements(path: Path) -> dict[str, list[str]]:
    """Return a mapping of case type -> required document names."""
    data = _read_json(path)
    case_types = data.get("case_types") if isinstance(data, dict) else None
    if not isinstance(case_types, dict) or not case_types:
        raise DataError(f"{path.name} must contain a non-empty 'case_types' object")

    requirements: dict[str, list[str]] = {}
    for case_type, spec in case_types.items():
        docs = spec.get("required_documents") if isinstance(spec, dict) else None
        if not isinstance(docs, list) or not docs or not all(isinstance(d, str) and d.strip() for d in docs):
            raise DataError(f"Case type '{case_type}' needs a non-empty 'required_documents' list of names")
        requirements[case_type] = docs
    return requirements


def load_filing_forms(path: Path) -> dict[str, list[str]]:
    """Return a mapping of case type -> government form(s) filed for it (used by the cover letter)."""
    case_types = _read_json(path).get("case_types", {})
    forms: dict[str, list[str]] = {}
    for case_type, spec in case_types.items():
        listed = spec.get("filing_forms") if isinstance(spec, dict) else None
        if not isinstance(listed, list) or not listed or not all(isinstance(f, str) and f.strip() for f in listed):
            raise DataError(f"Case type '{case_type}' needs a non-empty 'filing_forms' list")
        forms[case_type] = listed
    return forms


def parse_client(record: dict[str, Any]) -> Client:
    """Validate one raw client record and convert it to a Client."""
    if not isinstance(record, dict):
        raise DataError("Client record must be a JSON object")

    # An empty documents_received list is valid (nothing received yet); other fields must have a value.
    missing = [f for f in REQUIRED_CLIENT_FIELDS if record.get(f) is None or record.get(f) == ""]
    if missing:
        raise DataError(f"Client record is missing required field(s): {', '.join(missing)}")

    case_id = str(record["case_id"]).strip()
    if not CASE_ID_PATTERN.match(case_id):
        raise DataError(f"Invalid case_id '{case_id}': use letters, digits, '.', '_' or '-' only")

    raw_address = record["address"]
    if not isinstance(raw_address, dict):
        raise DataError("'address' must be an object with street, city, state and postal_code")
    missing_address = [f for f in REQUIRED_ADDRESS_FIELDS if not str(raw_address.get(f, "")).strip()]
    if missing_address:
        raise DataError(f"Address is missing field(s): {', '.join(missing_address)}")

    try:
        opened = date.fromisoformat(str(record["date_opened"]))
    except ValueError as exc:
        raise DataError(f"date_opened must be YYYY-MM-DD, got '{record['date_opened']}'") from exc

    received = record["documents_received"]
    if not isinstance(received, list) or not all(isinstance(d, str) for d in received):
        raise DataError("documents_received must be a list of document names")

    return Client(
        case_id=case_id,
        full_name=str(record["full_name"]).strip(),
        address=Address(**{f: str(raw_address[f]).strip() for f in REQUIRED_ADDRESS_FIELDS}),
        email=str(record["email"]).strip(),
        phone=str(record["phone"]).strip(),
        country_of_citizenship=str(record["country_of_citizenship"]).strip(),
        case_type=str(record["case_type"]).strip(),
        assigned_attorney=str(record["assigned_attorney"]).strip(),
        date_opened=opened,
        documents_received=tuple(received),
    )


def requirements_for(case_type: str, requirements: dict[str, list[str]]) -> list[str]:
    """Look up the required documents for a case type, failing clearly if it is unknown."""
    if case_type not in requirements:
        known = ", ".join(sorted(requirements))
        raise DataError(f"Unknown case type '{case_type}'. Known case types: {known}")
    return requirements[case_type]


def _normalize(name: str) -> str:
    """Compare document names ignoring case and extra whitespace."""
    return " ".join(name.split()).casefold()


def compute_document_status(required: list[str], received: list[str] | tuple[str, ...]) -> DocumentStatus:
    """Compare received documents against the requirements for a case type.

    Matching ignores case and whitespace. Missing and received lists keep the order of
    the requirements list so letters read consistently from client to client.
    """
    received_keys = {_normalize(d) for d in received if d.strip()}

    seen: set[str] = set()
    required_unique: list[str] = []
    for doc in required:
        key = _normalize(doc)
        if key not in seen:
            seen.add(key)
            required_unique.append(doc)

    have = [d for d in required_unique if _normalize(d) in received_keys]
    missing = [d for d in required_unique if _normalize(d) not in received_keys]

    unrecognized: list[str] = []
    flagged: set[str] = set()
    for doc in received:
        key = _normalize(doc)
        if key and key not in seen and key not in flagged:
            flagged.add(key)
            unrecognized.append(doc.strip())

    return DocumentStatus(required=required_unique, received=have, missing=missing, unrecognized=unrecognized)
