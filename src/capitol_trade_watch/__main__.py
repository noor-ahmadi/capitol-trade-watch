"""Command-line entry point for Capitol Trade Watch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from capitol_trade_watch import __version__
from capitol_trade_watch.alerts import AlertRenderError
from capitol_trade_watch.config import ConfigError, validate_config
from capitol_trade_watch.github_issues import GitHubIssueError
from capitol_trade_watch.health import HealthReportError
from capitol_trade_watch.house_index import HouseIndexError
from capitol_trade_watch.house_report import HouseReportError
from capitol_trade_watch.monitor import (
    MonitorError,
    check_for_new_filings,
    format_check_summary,
    format_preview,
    preview_new_filings,
    publish_test_alert,
    report_monitor_health,
)
from capitol_trade_watch.seed import seed_existing_filings
from capitol_trade_watch.state import StateError, StateStore

_DEFAULT_CONFIG = Path("config/tracked_people.toml")
_DEFAULT_STATE = Path("data/state.json")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="capitol-trade-watch",
        description="Keep an eye on congressional trade disclosures.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command")
    validate_parser = subparsers.add_parser(
        "validate-config",
        help="validate the tracked-people configuration",
    )
    validate_parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"configuration path (default: {_DEFAULT_CONFIG})",
    )
    seed_parser = subparsers.add_parser(
        "seed",
        help="silently remember filings that already exist",
    )
    seed_parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"configuration path (default: {_DEFAULT_CONFIG})",
    )
    seed_parser.add_argument(
        "--state",
        type=Path,
        default=_DEFAULT_STATE,
        help=f"state path (default: {_DEFAULT_STATE})",
    )
    seed_parser.add_argument(
        "--as-of",
        type=_iso_date,
        help="date used to choose index years (YYYY-MM-DD; default: today)",
    )
    preview_parser = subparsers.add_parser(
        "preview",
        help="render unseen filings without publishing or changing state",
    )
    preview_parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"configuration path (default: {_DEFAULT_CONFIG})",
    )
    preview_parser.add_argument(
        "--state",
        type=Path,
        default=_DEFAULT_STATE,
        help=f"state path (default: {_DEFAULT_STATE})",
    )
    preview_parser.add_argument(
        "--as-of",
        type=_iso_date,
        help="date used to choose index years (YYYY-MM-DD; default: today)",
    )
    check_parser = subparsers.add_parser(
        "check",
        help="publish unseen filings after a quiet seed",
    )
    check_parser.add_argument(
        "--config",
        type=Path,
        default=_DEFAULT_CONFIG,
        help=f"configuration path (default: {_DEFAULT_CONFIG})",
    )
    check_parser.add_argument(
        "--state",
        type=Path,
        default=_DEFAULT_STATE,
        help=f"state path (default: {_DEFAULT_STATE})",
    )
    check_parser.add_argument(
        "--as-of",
        type=_iso_date,
        help="date used to choose index years (YYYY-MM-DD; default: today)",
    )
    subparsers.add_parser(
        "test-alert",
        help="create a clearly synthetic GitHub issue to test notifications",
    )
    health_parser = subparsers.add_parser(
        "report-health",
        help="report a check job's success or failure through its health issue",
    )
    health_parser.add_argument("result", choices=("success", "failure"))
    status_parser = subparsers.add_parser(
        "status",
        help="show what the filing ledger remembers",
    )
    status_parser.add_argument(
        "--state",
        type=Path,
        default=_DEFAULT_STATE,
        help=f"state path (default: {_DEFAULT_STATE})",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    parser = build_parser()
    arguments = parser.parse_args(argv)

    if arguments.command == "validate-config":
        try:
            people = validate_config(arguments.config)
        except ConfigError as error:
            parser.error(str(error))
        print(f"Configuration is valid: {len(people)} tracked person(s).")
        return 0

    if arguments.command == "seed":
        try:
            summary = seed_existing_filings(
                arguments.config,
                arguments.state,
                as_of=arguments.as_of,
            )
        except (ConfigError, HouseIndexError, StateError) as error:
            parser.error(str(error))
        print(
            f"Seed complete: {summary.added} filing(s) added, "
            f"{summary.total} remembered in total."
        )
        return 0

    if arguments.command == "preview":
        try:
            preview = preview_new_filings(
                arguments.config,
                arguments.state,
                as_of=arguments.as_of,
            )
        except (
            AlertRenderError,
            ConfigError,
            HouseIndexError,
            HouseReportError,
            StateError,
        ) as error:
            parser.error(str(error))
        print(format_preview(preview), end="")
        return 0

    if arguments.command == "check":
        try:
            summary = check_for_new_filings(
                arguments.config,
                arguments.state,
                as_of=arguments.as_of,
            )
        except (
            AlertRenderError,
            ConfigError,
            GitHubIssueError,
            HouseIndexError,
            HouseReportError,
            MonitorError,
            StateError,
        ) as error:
            parser.error(str(error))
        print(format_check_summary(summary))
        return 0

    if arguments.command == "test-alert":
        try:
            result = publish_test_alert()
        except GitHubIssueError as error:
            parser.error(str(error))
        verb = "Created" if result.created else "Reused"
        print(
            f"{verb} synthetic test issue #{result.issue_number}: "
            f"{result.issue_url}"
        )
        return 0

    if arguments.command == "report-health":
        try:
            result = report_monitor_health(healthy=arguments.result == "success")
        except (GitHubIssueError, HealthReportError) as error:
            parser.error(str(error))
        if result is None:
            print("Monitor is healthy; no health issue needed.")
        else:
            print(f"Health issue #{result.issue_number}: {result.issue_url}")
        return 0

    if arguments.command == "status":
        try:
            state = StateStore(arguments.state).load()
        except StateError as error:
            parser.error(str(error))

        if not state.initialized:
            print("No seed has been saved yet.")
            return 0

        last_saved_check = (
            state.updated_at.isoformat().replace("+00:00", "Z")
            if state.updated_at is not None
            else "unknown"
        )
        print(
            f"Remembering {len(state.filings)} filing(s). "
            f"Last saved check: {last_saved_check}."
        )
        return 0

    parser.print_help()
    return 0


def _iso_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(
            "expected a date in YYYY-MM-DD form"
        ) from error
    if parsed.isoformat() != value:
        raise argparse.ArgumentTypeError("expected a date in YYYY-MM-DD form")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())
