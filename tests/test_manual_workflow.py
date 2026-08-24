from pathlib import Path


def test_manual_workflow_has_four_modes_and_no_schedule() -> None:
    workflow_path = (
        Path(__file__).parents[1]
        / ".github"
        / "workflows"
        / "manual-monitor.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "cron:" not in workflow
    for mode in ("preview", "seed", "check", "test-alert"):
        assert f"- {mode}" in workflow
        assert f"inputs.mode == '{mode}'" in workflow


def test_manual_jobs_use_pinned_actions_and_narrow_permissions() -> None:
    workflow_path = (
        Path(__file__).parents[1]
        / ".github"
        / "workflows"
        / "manual-monitor.yml"
    )
    workflow = workflow_path.read_text(encoding="utf-8")
    checkout = "actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803"
    setup_python = (
        "actions/setup-python@ece7cb06caefa5fff74198d8649806c4678c61a1"
    )

    assert "permissions: {}" in workflow
    assert workflow.count(checkout) == 4
    assert workflow.count(setup_python) == 4
    assert workflow.count("issues: write") == 2
    assert workflow.count("contents: write") == 2
    assert "Find and publish real filing alerts" in workflow
    assert "run: python -m capitol_trade_watch check" in workflow
    assert "Create synthetic test issue (no trade)" in workflow
