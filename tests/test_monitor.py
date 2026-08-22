from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from capitol_trade_watch.alerts import DisclosureAlert, filing_marker
from capitol_trade_watch.github_issues import GitHubIssueError, PublishResult
from capitol_trade_watch.house_index import HouseIndexResult, HouseIndexStatus
from capitol_trade_watch.house_report import HouseReportResult
from capitol_trade_watch.models import Filing, TrackedPerson, Transaction
from capitol_trade_watch.monitor import (
    build_test_alert,
    format_preview,
    preview_new_filings,
    publish_test_alert,
)
from capitol_trade_watch.state import (
    StateStore,
    TrackerState,
    record_results,
)


class FakeIndexClient:
    def __init__(
        self,
        results: tuple[HouseIndexResult, HouseIndexResult],
    ) -> None:
        self.results = results
        self.modified_since: dict[int, str] | None = None

    def fetch_recent(
        self,
        as_of: date,
        tracked_people: Iterable[TrackedPerson],
        *,
        modified_since: Mapping[int, str] | None = None,
    ) -> tuple[HouseIndexResult, HouseIndexResult]:
        assert as_of == date(2026, 8, 22)
        assert [person.id for person in tracked_people] == ["nancy-pelosi"]
        self.modified_since = dict(modified_since or {})
        return self.results


class FakeReportClient:
    def __init__(self) -> None:
        self.fetched: list[str] = []

    def fetch(self, filing: Filing) -> HouseReportResult:
        self.fetched.append(filing.document_id)
        transaction = Transaction(
            owner_code="SP",
            asset="Example Co. (EXM) [ST]",
            action="P",
            transaction_date=date(2026, 8, 1),
            notification_date=date(2026, 8, 2),
            amount_range="$1,001 - $15,000",
            capital_gains="No",
            description="Synthetic parser fixture.",
        )
        return HouseReportResult(
            filing=replace(filing, transactions=(transaction,))
        )


class FakePublisher:
    def __init__(self) -> None:
        self.alerts: list[DisclosureAlert] = []

    def publish(self, alert: DisclosureAlert) -> PublishResult:
        self.alerts.append(alert)
        return PublishResult(
            created=True,
            issue_number=12,
            issue_url="https://github.com/noor/project/issues/12",
        )


def test_preview_renders_unseen_filings_without_writing_state(
    tmp_path: Path,
) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    results = _results()
    index_client = FakeIndexClient(results)
    report_client = FakeReportClient()

    preview = preview_new_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 22),
        index_client=index_client,  # type: ignore[arg-type]
        report_client=report_client,  # type: ignore[arg-type]
    )

    assert [alert.document_id for alert in preview.alerts] == ["20040001"]
    assert report_client.fetched == ["20040001"]
    assert index_client.modified_since == {}
    assert not state_path.exists()
    output = format_preview(preview)
    assert "Preview only" in output
    assert "nothing was published" in output
    assert "Example Co." in output


def test_preview_skips_filings_already_in_the_ledger(tmp_path: Path) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    results = _results()
    saved = record_results(
        TrackerState(),
        results,
        observed_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
    )
    StateStore(state_path).save(saved)
    before = state_path.read_bytes()
    index_client = FakeIndexClient(results)
    report_client = FakeReportClient()

    preview = preview_new_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 22),
        index_client=index_client,  # type: ignore[arg-type]
        report_client=report_client,  # type: ignore[arg-type]
    )

    assert preview.alerts == ()
    assert report_client.fetched == []
    assert index_client.modified_since == {
        2026: "Fri, 21 Aug 2026 12:00:00 GMT"
    }
    assert state_path.read_bytes() == before
    assert "No unseen filings found." in format_preview(preview)


def test_test_alert_is_unmistakably_synthetic() -> None:
    alert = build_test_alert(
        run_id="12345",
        repository="noor/project",
        server_url="https://github.test",
    )

    assert alert.document_id == "99912345"
    assert alert.title == "[TEST] Notification delivery check (run 12345)"
    assert alert.body.count(filing_marker(alert.document_id)) == 1
    assert "This is synthetic" in alert.body
    assert "No congressional filing, transaction, or trade caused this issue" in alert.body
    assert "https://github.test/noor/project/actions/runs/12345" in alert.body


@pytest.mark.parametrize("run_id", ["", "0", "-1", "01", "not-a-run"])
def test_test_alert_rejects_invalid_run_ids(run_id: str) -> None:
    with pytest.raises(GitHubIssueError, match="positive integer"):
        build_test_alert(run_id=run_id, repository="noor/project")


def test_publish_test_alert_uses_the_workflow_run_identity() -> None:
    publisher = FakePublisher()

    result = publish_test_alert(
        environ={
            "GITHUB_RUN_ID": "12345",
            "GITHUB_REPOSITORY": "noor/project",
            "GITHUB_SERVER_URL": "https://github.test",
        },
        client=publisher,
    )

    assert result.issue_number == 12
    assert len(publisher.alerts) == 1
    assert publisher.alerts[0].title.startswith("[TEST]")
    assert publisher.alerts[0].document_id == "99912345"


def _results() -> tuple[HouseIndexResult, HouseIndexResult]:
    pelosi = TrackedPerson(
        id="nancy-pelosi",
        chamber="house",
        first_name="Nancy",
        last_name="Pelosi",
        state="CA",
    )
    filing = Filing(
        document_id="20040001",
        filer=pelosi,
        filing_date=date(2026, 8, 20),
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/"
            "ptr-pdfs/2026/20040001.pdf"
        ),
    )
    current = HouseIndexResult(
        year=2026,
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2026FD.zip"
        ),
        status=HouseIndexStatus.DOWNLOADED,
        filings=(filing,),
        last_modified="Fri, 21 Aug 2026 12:00:00 GMT",
    )
    prior = HouseIndexResult(
        year=2025,
        source_url=(
            "https://disclosures-clerk.house.gov/public_disc/financial-pdfs/2025FD.zip"
        ),
        status=HouseIndexStatus.NOT_MODIFIED,
    )
    return current, prior
