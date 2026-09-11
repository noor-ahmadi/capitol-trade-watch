from __future__ import annotations

import pytest

from capitol_trade_watch.health import (
    HEALTH_ISSUE_MARKER,
    HealthReportError,
    build_health_report,
)


@pytest.mark.parametrize("healthy", [False, True])
def test_health_report_links_to_the_run_and_identifies_the_outcome(
    healthy: bool,
) -> None:
    report = build_health_report(
        healthy=healthy,
        environ={
            "GITHUB_RUN_ID": "12345",
            "GITHUB_REPOSITORY": "noor/project",
            "GITHUB_SERVER_URL": "https://github.test/",
            "GITHUB_TOKEN": "private-token",
        },
    )

    assert report.healthy is healthy
    assert report.title.startswith("[HEALTH]")
    assert report.body.count(HEALTH_ISSUE_MARKER) == 1
    assert "https://github.test/noor/project/actions/runs/12345" in report.body
    assert "not a congressional filing alert" in report.body
    assert "private-token" not in report.body
    assert "house-ptr:" not in report.body
    if healthy:
        assert "recovered" in report.title
        assert "completed successfully" in report.body
    else:
        assert "needs attention" in report.title
        assert "Filing alerts may be delayed" in report.body


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("GITHUB_RUN_ID", ""),
        ("GITHUB_RUN_ID", "0"),
        ("GITHUB_RUN_ID", "-1"),
        ("GITHUB_RUN_ID", "not-a-run"),
        ("GITHUB_REPOSITORY", ""),
        ("GITHUB_REPOSITORY", "project"),
        ("GITHUB_REPOSITORY", "noor/"),
        ("GITHUB_SERVER_URL", "http://github.test"),
        ("GITHUB_SERVER_URL", "https://"),
        ("GITHUB_SERVER_URL", "https://["),
        ("GITHUB_SERVER_URL", "https://github.test/path"),
        ("GITHUB_SERVER_URL", "https://github.test?query=1"),
        ("GITHUB_SERVER_URL", "https://user:password@github.test"),
    ],
)
def test_health_report_rejects_invalid_run_context(name: str, value: str) -> None:
    environ = {"GITHUB_RUN_ID": "12345", "GITHUB_REPOSITORY": "noor/project"}
    environ[name] = value

    with pytest.raises(HealthReportError, match=name):
        build_health_report(healthy=False, environ=environ)
