"""Read-only filing previews and synthetic delivery checks."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
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
from capitol_trade_watch.house_index import HouseIndexClient
from capitol_trade_watch.house_report import HouseReportClient
from capitol_trade_watch.state import StateStore, unseen_filings


@dataclass(frozen=True, slots=True)
class PreviewResult:
    """Alerts rendered from unseen filings without changing saved state."""

    alerts: tuple[DisclosureAlert, ...]


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


def _required_environment(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise GitHubIssueError(f"required environment value is missing: {name}")
    return value
