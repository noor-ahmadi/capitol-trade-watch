"""Silently seed the filing ledger from the official House index."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

from capitol_trade_watch.config import load_tracked_people
from capitol_trade_watch.house_index import HouseIndexClient
from capitol_trade_watch.state import StateStore, record_results, unseen_filings


@dataclass(frozen=True, slots=True)
class SeedSummary:
    """Counts returned after a silent seed."""

    added: int
    total: int


def seed_existing_filings(
    config_path: str | Path,
    state_path: str | Path,
    *,
    as_of: date | None = None,
    observed_at: datetime | None = None,
    client: HouseIndexClient | None = None,
) -> SeedSummary:
    """Remember existing filings without publishing any alerts."""
    people = load_tracked_people(config_path)
    store = StateStore(state_path)
    state = store.load()
    index_client = client or HouseIndexClient()
    results = index_client.fetch_recent(
        as_of or date.today(),
        people,
        modified_since=state.sources,
    )

    added = len(unseen_filings(state, results))
    updated_state = record_results(
        state,
        results,
        observed_at=observed_at or datetime.now(UTC),
    )
    store.save(updated_state)
    return SeedSummary(added=added, total=len(updated_state.filings))
