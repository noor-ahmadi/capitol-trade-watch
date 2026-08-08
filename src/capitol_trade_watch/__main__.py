"""Command-line entry point for Capitol Trade Watch."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from capitol_trade_watch import __version__


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the command-line interface."""
    build_parser().parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
