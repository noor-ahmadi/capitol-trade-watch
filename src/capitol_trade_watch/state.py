"""Small JSON ledger for filings the tracker has already seen."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from capitol_trade_watch.house_index import HouseIndexResult
from capitol_trade_watch.models import Filing

SCHEMA_VERSION = 1


class StateError(RuntimeError):
    """Raised when saved tracker state is missing required or safe values."""


@dataclass(frozen=True, slots=True)
class SeenFiling:
    """The identifying details kept after a filing has been observed."""

    document_id: str
    person_id: str
    filing_year: int
    filing_date: date
    source_url: str
    first_seen_at: datetime


@dataclass(frozen=True, slots=True)
class TrackerState:
    """Everything needed to make later index checks idempotent."""

    initialized: bool = False
    sources: dict[int, str] = field(default_factory=dict)
    filings: dict[str, SeenFiling] = field(default_factory=dict)
    updated_at: datetime | None = None


class StateStore:
    """Load and atomically save the tracker's JSON ledger."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> TrackerState:
        """Return blank state when the ledger does not exist yet."""
        try:
            raw_document = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return TrackerState()
        except json.JSONDecodeError as error:
            raise StateError(
                f"invalid JSON in state file {self.path}: {error}"
            ) from error
        except OSError as error:
            raise StateError(
                f"could not read state file {self.path}: {error}"
            ) from error

        return _state_from_document(raw_document)

    def save(self, state: TrackerState) -> None:
        """Replace the ledger only after a complete new copy is on disk."""
        document = _document_from_state(state)
        contents = json.dumps(document, indent=2, sort_keys=True) + "\n"
        temporary_path = self.path.with_name(f"{self.path.name}.tmp")

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with temporary_path.open("w", encoding="utf-8", newline="\n") as state_file:
                state_file.write(contents)
                state_file.flush()
                os.fsync(state_file.fileno())
            os.replace(temporary_path, self.path)
        except OSError as error:
            raise StateError(
                f"could not save state file {self.path}: {error}"
            ) from error


def unseen_filings(
    state: TrackerState,
    results: Iterable[HouseIndexResult],
) -> tuple[Filing, ...]:
    """Return filings not in the ledger, with duplicate document IDs removed."""
    unseen: list[Filing] = []
    found_document_ids = set(state.filings)
    for result in results:
        for filing in result.filings:
            if filing.document_id in found_document_ids:
                continue
            found_document_ids.add(filing.document_id)
            unseen.append(filing)
    return tuple(unseen)


def record_results(
    state: TrackerState,
    results: Iterable[HouseIndexResult],
    *,
    observed_at: datetime,
) -> TrackerState:
    """Return state containing every filing and source timestamp in the results."""
    observed_at = _as_utc(observed_at, "observed_at")
    sources = dict(state.sources)
    filings = dict(state.filings)

    for result in results:
        if result.last_modified:
            sources[result.year] = result.last_modified

        for filing in result.filings:
            existing = filings.get(filing.document_id)
            if existing is not None:
                _check_same_filing(existing, filing, result.year)
                continue

            filings[filing.document_id] = SeenFiling(
                document_id=filing.document_id,
                person_id=filing.filer.id,
                filing_year=result.year,
                filing_date=filing.filing_date,
                source_url=filing.source_url,
                first_seen_at=observed_at,
            )

    return TrackerState(
        initialized=True,
        sources=sources,
        filings=filings,
        updated_at=observed_at,
    )


def _check_same_filing(existing: SeenFiling, filing: Filing, filing_year: int) -> None:
    details = (
        existing.person_id,
        existing.filing_year,
        existing.filing_date,
        existing.source_url,
    )
    current_details = (
        filing.filer.id,
        filing_year,
        filing.filing_date,
        filing.source_url,
    )
    if details != current_details:
        raise StateError(
            f"House document {filing.document_id} conflicts with the saved filing"
        )


def _state_from_document(raw_document: Any) -> TrackerState:
    if not isinstance(raw_document, Mapping):
        raise StateError("state file must contain a JSON object")

    version = raw_document.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise StateError(
            f"unsupported state schema {version!r}; expected {SCHEMA_VERSION}"
        )

    initialized = raw_document.get("initialized")
    if not isinstance(initialized, bool):
        raise StateError("state field 'initialized' must be true or false")

    raw_sources = raw_document.get("sources")
    if not isinstance(raw_sources, Mapping):
        raise StateError("state field 'sources' must be an object")
    sources: dict[int, str] = {}
    for raw_year, raw_timestamp in raw_sources.items():
        if (
            not isinstance(raw_year, str)
            or not raw_year.isdigit()
            or len(raw_year) != 4
        ):
            raise StateError(f"invalid filing year in state sources: {raw_year!r}")
        if not isinstance(raw_timestamp, str) or not raw_timestamp.strip():
            raise StateError(f"source timestamp for {raw_year} must be non-empty")
        sources[int(raw_year)] = raw_timestamp

    raw_filings = raw_document.get("filings")
    if not isinstance(raw_filings, Mapping):
        raise StateError("state field 'filings' must be an object")
    filings: dict[str, SeenFiling] = {}
    for document_id, raw_filing in raw_filings.items():
        if not isinstance(document_id, str) or not document_id.isdigit():
            raise StateError(f"invalid House document ID in state: {document_id!r}")
        if not isinstance(raw_filing, Mapping):
            raise StateError(f"saved filing {document_id} must be an object")
        filing = _seen_filing_from_document(raw_filing)
        if filing.document_id != document_id:
            raise StateError(
                f"saved filing key {document_id} does not match its document ID"
            )
        filings[document_id] = filing

    raw_updated_at = raw_document.get("updated_at")
    updated_at = (
        None
        if raw_updated_at is None
        else _parse_datetime(raw_updated_at, "updated_at")
    )
    return TrackerState(
        initialized=initialized,
        sources=sources,
        filings=filings,
        updated_at=updated_at,
    )


def _seen_filing_from_document(raw_filing: Mapping[str, Any]) -> SeenFiling:
    document_id = _required_text(raw_filing, "document_id")
    if not document_id.isdigit():
        raise StateError(f"invalid saved House document ID: {document_id!r}")

    filing_year = raw_filing.get("filing_year")
    if type(filing_year) is not int or not 1900 <= filing_year <= 9999:
        raise StateError(f"invalid filing year for document {document_id}")

    raw_filing_date = raw_filing.get("filing_date")
    if not isinstance(raw_filing_date, str):
        raise StateError(f"invalid filing date for document {document_id}")
    try:
        filing_date = date.fromisoformat(raw_filing_date)
    except ValueError as error:
        raise StateError(f"invalid filing date for document {document_id}") from error

    return SeenFiling(
        document_id=document_id,
        person_id=_required_text(raw_filing, "person_id"),
        filing_year=filing_year,
        filing_date=filing_date,
        source_url=_required_text(raw_filing, "source_url"),
        first_seen_at=_parse_datetime(
            raw_filing.get("first_seen_at"),
            f"first_seen_at for document {document_id}",
        ),
    )


def _document_from_state(state: TrackerState) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "initialized": state.initialized,
        "sources": {str(year): timestamp for year, timestamp in state.sources.items()},
        "filings": {
            document_id: {
                "document_id": filing.document_id,
                "person_id": filing.person_id,
                "filing_year": filing.filing_year,
                "filing_date": filing.filing_date.isoformat(),
                "source_url": filing.source_url,
                "first_seen_at": _format_datetime(filing.first_seen_at),
            }
            for document_id, filing in state.filings.items()
        },
        "updated_at": (
            _format_datetime(state.updated_at) if state.updated_at is not None else None
        ),
    }


def _required_text(document: Mapping[str, Any], field_name: str) -> str:
    value = document.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise StateError(f"saved filing field {field_name!r} must be non-empty")
    return value.strip()


def _parse_datetime(value: Any, field_name: str) -> datetime:
    if not isinstance(value, str):
        raise StateError(f"state field {field_name!r} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise StateError(
            f"state field {field_name!r} has an invalid timestamp"
        ) from error
    return _as_utc(parsed, field_name)


def _format_datetime(value: datetime) -> str:
    normalized = _as_utc(value, "timestamp")
    return normalized.isoformat().replace("+00:00", "Z")


def _as_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise StateError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)
