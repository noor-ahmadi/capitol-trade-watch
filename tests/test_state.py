from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from capitol_trade_watch.house_index import (
    HouseIndexResult,
    HouseIndexStatus,
)
from capitol_trade_watch.models import Filing, TrackedPerson
from capitol_trade_watch.state import (
    StateError,
    StateStore,
    TrackerState,
    record_results,
    unseen_filings,
)


def test_missing_state_file_starts_blank(tmp_path: Path) -> None:
    state = StateStore(tmp_path / "missing" / "state.json").load()

    assert state == TrackerState()


def test_state_round_trip_is_readable_and_atomic(tmp_path: Path) -> None:
    state_path = tmp_path / "data" / "state.json"
    observed_at = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
    state = record_results(TrackerState(), [_result()], observed_at=observed_at)

    store = StateStore(state_path)
    store.save(state)

    assert store.load() == state
    assert not state_path.with_name("state.json.tmp").exists()
    document = json.loads(state_path.read_text(encoding="utf-8"))
    assert document["schema_version"] == 1
    assert document["updated_at"] == "2026-08-10T14:30:00Z"
    assert document["filings"]["20030630"]["person_id"] == "nancy-pelosi"


def test_unknown_state_schema_is_rejected(tmp_path: Path) -> None:
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "schema_version": 99,
                "initialized": False,
                "sources": {},
                "filings": {},
                "updated_at": None,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(StateError, match="unsupported state schema"):
        StateStore(state_path).load()


def test_recording_results_is_idempotent() -> None:
    result = _result()
    first_seen = datetime(2026, 8, 10, 14, 30, tzinfo=UTC)
    checked_again = datetime(2026, 8, 11, 9, 15, tzinfo=UTC)

    document_ids = [
        filing.document_id
        for filing in unseen_filings(TrackerState(), [result])
    ]
    assert document_ids == ["20030630"]

    first_state = record_results(TrackerState(), [result], observed_at=first_seen)
    second_state = record_results(first_state, [result], observed_at=checked_again)

    assert unseen_filings(first_state, [result]) == ()
    assert len(second_state.filings) == 1
    assert second_state.filings["20030630"].first_seen_at == first_seen
    assert second_state.sources == {2025: "Fri, 08 Aug 2025 12:00:00 GMT"}
    assert second_state.updated_at == checked_again


def test_recording_requires_a_timezone() -> None:
    with pytest.raises(StateError, match="must include a timezone"):
        record_results(
            TrackerState(),
            [_result()],
            observed_at=datetime(2026, 8, 10, 14, 30),
        )


def _result() -> HouseIndexResult:
    pelosi = TrackedPerson(
        id="nancy-pelosi",
        chamber="house",
        first_name="Nancy",
        last_name="Pelosi",
        state="CA",
    )
    filing = Filing(
        document_id="20030630",
        filer=pelosi,
        filing_date=date(2025, 7, 9),
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/"
            "ptr-pdfs/2025/20030630.pdf"
        ),
    )
    return HouseIndexResult(
        year=2025,
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.zip"
        ),
        status=HouseIndexStatus.DOWNLOADED,
        filings=(filing,),
        last_modified="Fri, 08 Aug 2025 12:00:00 GMT",
    )
