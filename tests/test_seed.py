from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime
from pathlib import Path

from capitol_trade_watch.house_index import HouseIndexResult, HouseIndexStatus
from capitol_trade_watch.models import Filing, TrackedPerson
from capitol_trade_watch.seed import seed_existing_filings
from capitol_trade_watch.state import StateStore


class FakeIndexClient:
    def __init__(self) -> None:
        self.source_timestamps: list[dict[int, str]] = []

    def fetch_recent(
        self,
        as_of: date,
        tracked_people: Iterable[TrackedPerson],
        *,
        modified_since: Mapping[int, str] | None = None,
    ) -> tuple[HouseIndexResult, HouseIndexResult]:
        people = tuple(tracked_people)
        assert as_of == date(2026, 8, 10)
        assert [person.id for person in people] == ["nancy-pelosi"]
        self.source_timestamps.append(dict(modified_since or {}))
        return _results(people[0])


def test_seed_is_silent_and_idempotent(tmp_path: Path) -> None:
    project_root = Path(__file__).parents[1]
    config_path = project_root / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    client = FakeIndexClient()
    first_seen = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)
    checked_again = datetime(2026, 8, 11, 15, 0, tzinfo=UTC)

    first = seed_existing_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 10),
        observed_at=first_seen,
        client=client,  # type: ignore[arg-type]
    )
    second = seed_existing_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 10),
        observed_at=checked_again,
        client=client,  # type: ignore[arg-type]
    )

    state = StateStore(state_path).load()
    assert (first.added, first.total) == (1, 1)
    assert (second.added, second.total) == (0, 1)
    assert state.initialized is True
    assert state.filings["20030630"].first_seen_at == first_seen
    assert client.source_timestamps == [
        {},
        {
            2025: "Fri, 08 Aug 2025 12:00:00 GMT",
            2026: "Mon, 10 Aug 2026 12:00:00 GMT",
        },
    ]


def _results(
    pelosi: TrackedPerson,
) -> tuple[HouseIndexResult, HouseIndexResult]:
    current = HouseIndexResult(
        year=2026,
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.zip"
        ),
        status=HouseIndexStatus.DOWNLOADED,
        last_modified="Mon, 10 Aug 2026 12:00:00 GMT",
    )
    prior = HouseIndexResult(
        year=2025,
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.zip"
        ),
        status=HouseIndexStatus.DOWNLOADED,
        filings=(
            Filing(
                document_id="20030630",
                filer=pelosi,
                filing_date=date(2025, 7, 9),
                source_url=(
                    "https://disclosures-clerk.house.gov/public_disc/"
                    "ptr-pdfs/2025/20030630.pdf"
                ),
            ),
        ),
        last_modified="Fri, 08 Aug 2025 12:00:00 GMT",
    )
    return current, prior
