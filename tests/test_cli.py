from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

import capitol_trade_watch.__main__ as cli
from capitol_trade_watch import __version__
from capitol_trade_watch.__main__ import main
from capitol_trade_watch.alerts import DisclosureAlert
from capitol_trade_watch.github_issues import PublishResult
from capitol_trade_watch.monitor import CheckSummary, PreviewResult
from capitol_trade_watch.seed import SeedSummary
from capitol_trade_watch.state import SeenFiling, StateStore, TrackerState


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


def test_preview_command_prints_without_changing_state(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}
    alert = DisclosureAlert(
        document_id="20040001",
        title="Nancy Pelosi PTR 20040001 (filed 2026-08-20)",
        body="preview body\n",
    )

    def fake_preview(
        config_path: Path,
        state_path: Path,
        *,
        as_of: date | None,
    ) -> PreviewResult:
        captured.update(config_path=config_path, state_path=state_path, as_of=as_of)
        return PreviewResult(alerts=(alert,))

    monkeypatch.setattr(cli, "preview_new_filings", fake_preview)

    assert main(["preview", "--as-of", "2026-08-22"]) == 0
    assert captured == {
        "config_path": Path("config/tracked_people.toml"),
        "state_path": Path("data/state.json"),
        "as_of": date(2026, 8, 22),
    }
    output = capsys.readouterr().out
    assert "Preview only" in output
    assert alert.title in output
    assert "preview body" in output


def test_check_command_reports_real_alert_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    captured: dict[str, object] = {}

    def fake_check(
        config_path: Path,
        state_path: Path,
        *,
        as_of: date | None,
    ) -> CheckSummary:
        captured.update(config_path=config_path, state_path=state_path, as_of=as_of)
        return CheckSummary(
            new_filings=2,
            created_issues=1,
            reused_issues=1,
            remembered_filings=7,
            state_saved=True,
        )

    monkeypatch.setattr(cli, "check_for_new_filings", fake_check)

    assert main(["check", "--as-of", "2026-08-22"]) == 0
    assert captured == {
        "config_path": Path("config/tracked_people.toml"),
        "state_path": Path("data/state.json"),
        "as_of": date(2026, 8, 22),
    }
    assert capsys.readouterr().out.strip() == (
        "Check complete: 2 new filing(s), 1 issue(s) created, "
        "1 reused, 7 remembered in total. Ledger saved."
    )


def test_test_alert_command_reports_the_synthetic_issue(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        cli,
        "publish_test_alert",
        lambda: PublishResult(
            created=True,
            issue_number=12,
            issue_url="https://github.com/noor/project/issues/12",
        ),
    )

    assert main(["test-alert"]) == 0
    assert capsys.readouterr().out.strip() == (
        "Created synthetic test issue #12: "
        "https://github.com/noor/project/issues/12"
    )


def test_status_command_shows_an_unseeded_ledger(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path = tmp_path / "state.json"

    assert main(["status", "--state", str(state_path)]) == 0
    assert capsys.readouterr().out.strip() == "No seed has been saved yet."


def test_status_command_shows_saved_filings(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    state_path = tmp_path / "state.json"
    checked_at = datetime(2026, 8, 11, 13, 45, tzinfo=UTC)
    StateStore(state_path).save(
        TrackerState(
            initialized=True,
            filings={
                "20030630": SeenFiling(
                    document_id="20030630",
                    person_id="nancy-pelosi",
                    filing_year=2025,
                    filing_date=date(2025, 7, 9),
                    source_url=(
                        "https://disclosures-clerk.house.gov/public_disc/"
                        "ptr-pdfs/2025/20030630.pdf"
                    ),
                    first_seen_at=checked_at,
                )
            },
            updated_at=checked_at,
        )
    )

    assert main(["status", "--state", str(state_path)]) == 0
    assert capsys.readouterr().out.strip() == (
        "Remembering 1 filing(s). Last saved check: 2026-08-11T13:45:00Z."
    )
