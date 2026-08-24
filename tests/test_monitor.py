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
    MonitorError,
    build_test_alert,
    check_for_new_filings,
    format_check_summary,
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
    def __init__(
        self,
        *,
        created: bool = True,
        fail_at: int | None = None,
    ) -> None:
        self.created = created
        self.fail_at = fail_at
        self.alerts: list[DisclosureAlert] = []

    def publish(self, alert: DisclosureAlert) -> PublishResult:
        self.alerts.append(alert)
        if self.fail_at == len(self.alerts):
            raise GitHubIssueError("synthetic publishing failure")
        return PublishResult(
            created=self.created,
            issue_number=11 + len(self.alerts),
            issue_url=(
                "https://github.com/noor/project/issues/"
                f"{11 + len(self.alerts)}"
            ),
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


def test_real_check_refuses_to_run_before_a_quiet_seed(tmp_path: Path) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    index_client = FakeIndexClient(_results())

    with pytest.raises(MonitorError, match="seed"):
        check_for_new_filings(
            config_path,
            state_path,
            as_of=date(2026, 8, 22),
            index_client=index_client,  # type: ignore[arg-type]
        )

    assert index_client.modified_since is None
    assert not state_path.exists()


def test_real_check_publishes_then_records_an_unseen_filing(
    tmp_path: Path,
) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    checked_at = datetime(2026, 8, 22, 14, 30, tzinfo=UTC)
    StateStore(state_path).save(
        TrackerState(
            initialized=True,
            updated_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        )
    )
    index_client = FakeIndexClient(_results())
    report_client = FakeReportClient()
    publisher = FakePublisher()

    summary = check_for_new_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 22),
        observed_at=checked_at,
        index_client=index_client,  # type: ignore[arg-type]
        report_client=report_client,  # type: ignore[arg-type]
        issue_client=publisher,
    )

    assert summary.new_filings == 1
    assert summary.created_issues == 1
    assert summary.reused_issues == 0
    assert summary.remembered_filings == 1
    assert report_client.fetched == ["20040001"]
    assert publisher.alerts[0].document_id == "20040001"
    saved = StateStore(state_path).load()
    assert list(saved.filings) == ["20040001"]
    assert saved.updated_at == checked_at
    assert format_check_summary(summary) == (
        "Check complete: 1 new filing(s), 1 issue(s) created, "
        "0 reused, 1 remembered in total."
    )


def test_real_check_records_an_idempotently_reused_issue(tmp_path: Path) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    StateStore(state_path).save(TrackerState(initialized=True))

    summary = check_for_new_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 22),
        observed_at=datetime(2026, 8, 22, 15, 0, tzinfo=UTC),
        index_client=FakeIndexClient(_results()),  # type: ignore[arg-type]
        report_client=FakeReportClient(),  # type: ignore[arg-type]
        issue_client=FakePublisher(created=False),
    )

    assert (summary.created_issues, summary.reused_issues) == (0, 1)
    assert list(StateStore(state_path).load().filings) == ["20040001"]


def test_real_check_does_not_save_state_after_a_publish_failure(
    tmp_path: Path,
) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    StateStore(state_path).save(TrackerState(initialized=True))
    before = state_path.read_bytes()
    first = _results()[0].filings[0]
    second = replace(
        first,
        document_id="20040002",
        filing_date=date(2026, 8, 21),
        source_url=first.source_url.replace("20040001", "20040002"),
    )
    current, prior = _results()
    results = (replace(current, filings=(first, second)), prior)
    publisher = FakePublisher(fail_at=2)

    with pytest.raises(GitHubIssueError, match="publishing failure"):
        check_for_new_filings(
            config_path,
            state_path,
            as_of=date(2026, 8, 22),
            observed_at=datetime(2026, 8, 22, 15, 30, tzinfo=UTC),
            index_client=FakeIndexClient(results),  # type: ignore[arg-type]
            report_client=FakeReportClient(),  # type: ignore[arg-type]
            issue_client=publisher,
        )

    assert [alert.document_id for alert in publisher.alerts] == [
        "20040001",
        "20040002",
    ]
    assert state_path.read_bytes() == before


def test_real_check_with_no_new_filings_does_not_need_an_issue_token(
    tmp_path: Path,
) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"
    state_path = tmp_path / "state.json"
    results = _results()
    StateStore(state_path).save(
        record_results(
            TrackerState(),
            results,
            observed_at=datetime(2026, 8, 21, 12, 0, tzinfo=UTC),
        )
    )
    checked_at = datetime(2026, 8, 22, 16, 0, tzinfo=UTC)

    summary = check_for_new_filings(
        config_path,
        state_path,
        as_of=date(2026, 8, 22),
        observed_at=checked_at,
        environ={},
        index_client=FakeIndexClient(results),  # type: ignore[arg-type]
    )

    assert summary.new_filings == 0
    assert summary.created_issues == 0
    assert summary.reused_issues == 0
    assert summary.remembered_filings == 1
    assert StateStore(state_path).load().updated_at == checked_at


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
