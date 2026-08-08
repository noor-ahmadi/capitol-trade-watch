from __future__ import annotations

from pathlib import Path

import pytest

from capitol_trade_watch import __version__
from capitol_trade_watch.__main__ import main


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
