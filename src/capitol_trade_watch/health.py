"""Build monitor health notices linked to their GitHub Actions run."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import quote, urlsplit

HEALTH_ISSUE_MARKER = "<!-- capitol-trade-watch:monitor-health -->"


class HealthReportError(ValueError):
    """Raised when a health notice cannot identify its workflow run."""


@dataclass(frozen=True, slots=True)
class HealthReport:
    """The issue text and desired health state for a completed check job."""

    healthy: bool
    title: str
    body: str


def build_health_report(
    *,
    healthy: bool,
    environ: Mapping[str, str] | None = None,
) -> HealthReport:
    """Describe a failure or recovery without copying logs into a public issue."""
    values = os.environ if environ is None else environ
    run_id = _required_environment(values, "GITHUB_RUN_ID")
    if not run_id.isascii() or not run_id.isdigit() or int(run_id) <= 0:
        raise HealthReportError("GITHUB_RUN_ID must be a positive integer")

    repository = _required_environment(values, "GITHUB_REPOSITORY")
    parts = repository.split("/")
    if len(parts) != 2 or not all(parts):
        raise HealthReportError("GITHUB_REPOSITORY must use OWNER/REPOSITORY")

    server_url = values.get("GITHUB_SERVER_URL", "https://github.com").rstrip("/")
    try:
        server = urlsplit(server_url)
    except ValueError as error:
        raise HealthReportError(
            "GITHUB_SERVER_URL must be an HTTPS server URL"
        ) from error
    if (
        server.scheme != "https"
        or not server.hostname
        or server.username is not None
        or server.password is not None
        or server.path
        or server.query
        or server.fragment
    ):
        raise HealthReportError("GITHUB_SERVER_URL must be an HTTPS server URL")
    repository_path = "/".join(quote(part, safe="") for part in parts)
    run_url = f"{server_url}/{repository_path}/actions/runs/{int(run_id)}"

    title = (
        "[HEALTH] Filing monitor recovered"
        if healthy
        else "[HEALTH] Filing monitor needs attention"
    )
    message = (
        "The filing check and ledger save steps completed successfully."
        if healthy
        else (
            "The filing check or ledger save failed. Filing alerts may be "
            "delayed until a check completes successfully."
        )
    )
    return HealthReport(
        healthy=healthy,
        title=title,
        body="\n".join(
            [
                HEALTH_ISSUE_MARKER,
                "",
                message,
                "",
                f"[Open the workflow run and its logs]({run_url})",
                "",
                "This is a monitor status notice, not a congressional filing alert.",
                "",
            ]
        ),
    )


def _required_environment(values: Mapping[str, str], name: str) -> str:
    value = values.get(name, "").strip()
    if not value:
        raise HealthReportError(f"required environment value is missing: {name}")
    return value
