"""Filing previews, real checks, and synthetic delivery tests."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Protocol

from capitol_trade_watch.alerts import (
    DisclosureAlert,
    filing_marker,
    render_disclosure_alert,
)
from capitol_trade_watch.config import load_tracked_people
from capitol_trade_watch.github_issues import (
    GitHubIssueClient,
    GitHubIssueError,
    PublishResult,
)
from capitol_trade_watch.health import build_health_report
from capitol_trade_watch.house_index import HouseIndexClient
from capitol_trade_watch.house_report import HouseReportClient
from capitol_trade_watch.state import StateStore, record_results, unseen_filings

_HEARTBEAT_INTERVAL = timedelta(days=1)


class MonitorError(RuntimeError):
    """Raised when a real filing check is not safe to run."""


@dataclass(frozen=True, slots=True)
class PreviewResult:
    """Alerts rendered from unseen filings without changing saved state."""

    alerts: tuple[DisclosureAlert, ...]


@dataclass(frozen=True, slots=True)
class CheckSummary:
    """Counts and ledger save status from one completed real filing check."""

    new_filings: int
    created_issues: int
    reused_issues: int
    remembered_filings: int
    state_saved: bool


class _IssuePublisher(Protocol):
    def publish(self, alert: DisclosureAlert) -> PublishResult:
        """Publish or reuse one alert."""


def preview_new_filings(
    config_path: str | Path,
    state_path: str | Path,
    *,
    as_of: date | None = None,
    index_client: HouseIndexClient | None = None,
    report_client: HouseReportClient | None = None,
) -> PreviewResult:
    """Render unseen filings without publishing or updating the ledger."""
    people = load_tracked_people(config_path)
    state = StateStore(state_path).load()
    results = (index_client or HouseIndexClient()).fetch_recent(
        as_of or date.today(),
        people,
        modified_since=state.sources,
    )
    reports = report_client or HouseReportClient()
    alerts = tuple(
        render_disclosure_alert(reports.fetch(filing))
        for filing in unseen_filings(state, results)
    )
    return PreviewResult(alerts=alerts)


def check_for_new_filings(
    config_path: str | Path,
    state_path: str | Path,
    *,
    as_of: date | None = None,
    observed_at: datetime | None = None,
    environ: Mapping[str, str] | None = None,
    index_client: HouseIndexClient | None = None,
    report_client: HouseReportClient | None = None,
    issue_client: _IssuePublisher | None = None,
) -> CheckSummary:
    """Publish unseen filings and save state only after every alert succeeds.

    Quiet checks save a heartbeat at most once every 24 hours.
    """
    people = load_tracked_people(config_path)
    store = StateStore(state_path)
    state = store.load()
    if not state.initialized:
        raise MonitorError(
            "the filing ledger has not been seeded; run the seed command first"
        )

    results = (index_client or HouseIndexClient()).fetch_recent(
        as_of or date.today(),
        people,
        modified_since=state.sources,
    )
    unseen = unseen_filings(state, results)
    checked_at = observed_at or datetime.now(UTC)
    updated_state = record_results(
        state,
        results,
        observed_at=checked_at,
    )

    publish_results: list[PublishResult] = []
    if unseen:
        reports = report_client or HouseReportClient()
        publisher = issue_client or GitHubIssueClient.from_environment(
            environ=environ
        )
        for filing in unseen:
            alert = render_disclosure_alert(reports.fetch(filing))
            publish_results.append(publisher.publish(alert))

    state_saved = (
        bool(unseen)
        or state.updated_at is None
        or checked_at - state.updated_at >= _HEARTBEAT_INTERVAL
    )
    if state_saved:
        store.save(updated_state)
    return CheckSummary(
        new_filings=len(unseen),
        created_issues=sum(result.created for result in publish_results),
        reused_issues=sum(not result.created for result in publish_results),
        remembered_filings=len(updated_state.filings),
        state_saved=state_saved,
    )


def format_check_summary(summary: CheckSummary) -> str:
    """Format the counts and ledger save status from a real filing check."""
    ledger_status = (
        "Ledger saved."
        if summary.state_saved
        else "Ledger unchanged; daily heartbeat not due."
    )
    return (
        f"Check complete: {summary.new_filings} new filing(s), "
        f"{summary.created_issues} issue(s) created, "
        f"{summary.reused_issues} reused, "
        f"{summary.remembered_filings} remembered in total. {ledger_status}"
    )


def format_preview(preview: PreviewResult) -> str:
    """Format a preview for a terminal or GitHub job summary."""
    lines = [
        "# Capitol Trade Watch preview",
        "",
        "> Preview only: nothing was published and the filing ledger was not changed.",
    ]
    if not preview.alerts:
        lines.extend(["", "No unseen filings found."])
    else:
        for alert in preview.alerts:
            lines.extend(
                [
                    "",
                    "---",
                    "",
                    f"## {alert.title}",
                    "",
                    alert.body.rstrip(),
                ]
            )
    return "\n".join(lines) + "\n"


def build_test_alert(
    *,
    run_id: str,
    repository: str,
    server_url: str = "https://github.com",
) -> DisclosureAlert:
    """Build a clearly synthetic alert tied to one workflow run."""
    normalized_run_id = run_id.strip()
    if (
        not normalized_run_id.isdigit()
        or int(normalized_run_id) <= 0
        or str(int(normalized_run_id)) != normalized_run_id
    ):
        raise GitHubIssueError("GITHUB_RUN_ID must be a positive integer")

    repository_parts = repository.strip().split("/")
    if len(repository_parts) != 2 or not all(repository_parts):
        raise GitHubIssueError(
            "GITHUB_REPOSITORY must use the OWNER/REPOSITORY form"
        )
    normalized_repository = "/".join(repository_parts)

    normalized_server_url = server_url.rstrip("/")
    if not normalized_server_url.startswith("https://"):
        raise GitHubIssueError("GITHUB_SERVER_URL must use HTTPS")

    synthetic_document_id = f"999{normalized_run_id}"
    run_url = (
        f"{normalized_server_url}/{normalized_repository}/actions/runs/"
        f"{normalized_run_id}"
    )
    body = "\n".join(
        [
            filing_marker(synthetic_document_id),
            "",
            "# Delivery test",
            "",
            (
                "**This is synthetic. No congressional filing, transaction, "
                "or trade caused this issue.**"
            ),
            "",
            (
                "It was created by the manual `test-alert` mode so notification "
                "delivery can be checked safely."
            ),
            "",
            f"[Open workflow run {normalized_run_id}]({run_url})",
            "",
            "Close or delete this issue after the notification arrives.",
        ]
    )
    return DisclosureAlert(
        document_id=synthetic_document_id,
        title=f"[TEST] Notification delivery check (run {normalized_run_id})",
        body=body + "\n",
    )


def publish_test_alert(
    *,
    environ: Mapping[str, str] | None = None,
    client: _IssuePublisher | None = None,
) -> PublishResult:
    """Publish one synthetic alert using GitHub Actions environment values."""
    values = os.environ if environ is None else environ
    alert = build_test_alert(
        run_id=_required_environment(values, "GITHUB_RUN_ID"),
        repository=_required_environment(values, "GITHUB_REPOSITORY"),
        server_url=values.get("GITHUB_SERVER_URL", "https://github.com"),
    )
    publisher = client or GitHubIssueClient.from_environment(environ=values)
    return publisher.publish(alert)


def report_monitor_health(
    *,
    healthy: bool,
    environ: Mapping[str, str] | None = None,
    client: GitHubIssueClient | None = None,
) -> PublishResult | None:
    """Report the check job result without reading or changing the filing ledger."""
    report = build_health_report(healthy=healthy, environ=environ)
    publisher = client or GitHubIssueClient.from_environment(environ=environ)
    return publisher.report_health(report)


def _required_environment(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise GitHubIssueError(f"required environment value is missing: {name}")
    return value
