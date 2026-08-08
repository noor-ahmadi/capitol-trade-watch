"""Command-line entry point for Capitol Trade Watch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from capitol_trade_watch import __version__
from capitol_trade_watch.config import ConfigError, validate_config

_DEFAULT_CONFIG = Path("config/tracked_people.toml")


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        prog="capitol-trade-watch",
        description="Congressional disclosure alert tracker (scaffolding only).",
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

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
