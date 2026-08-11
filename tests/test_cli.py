from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

import capitol_trade_watch.__main__ as cli
from capitol_trade_watch import __version__
from capitol_trade_watch.__main__ import main
from capitol_trade_watch.seed import SeedSummary


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == f"capitol-trade-watch {__version__}"


def test_validate_config_command(capsys: pytest.CaptureFixture[str]) -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"

    assert main(["validate-config", "--config", str(config_path)]) == 0
    assert capsys.readouterr().out.strip() == (
        "Configuration is valid: 1 tracked person(s)."
    )


def test_seed_command_reports_counts_without_sending_anything(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_seed(
        config_path: Path,
        state_path: Path,
        *,
        as_of: date | None,
    ) -> SeedSummary:
        captured.update(config_path=config_path, state_path=state_path, as_of=as_of)
        return SeedSummary(added=5, total=5)

    monkeypatch.setattr(cli, "seed_existing_filings", fake_seed)

    assert main(["seed", "--as-of", "2026-08-10"]) == 0
    assert captured == {
        "config_path": Path("config/tracked_people.toml"),
        "state_path": Path("data/state.json"),
        "as_of": date(2026, 8, 10),
    }
    assert capsys.readouterr().out.strip() == (
        "Seed complete: 5 filing(s) added, 5 remembered in total."
    )
