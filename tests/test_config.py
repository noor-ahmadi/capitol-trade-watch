from __future__ import annotations

from pathlib import Path

import pytest

from capitol_trade_watch.config import ConfigError, load_tracked_people


def test_loads_pelosi_configuration() -> None:
    config_path = Path(__file__).parents[1] / "config" / "tracked_people.toml"

    people = load_tracked_people(config_path)

    assert len(people) == 1
    assert people[0].id == "nancy-pelosi"
    assert people[0].display_name == "Nancy Pelosi"
    assert people[0].chamber == "house"
    assert people[0].state == "CA"


@pytest.mark.parametrize(
    ("document", "message"),
    [
        (
            """[[people]]
id = "nancy-pelosi"
chamber = "senate"
first_name = "Nancy"
last_name = "Pelosi"
state = "CA"
""",
            "unsupported chamber",
        ),
        (
            """[[people]]
id = "nancy-pelosi"
chamber = "house"
first_name = ""
last_name = "Pelosi"
state = "CA"
""",
            "requires a non-empty first_name",
        ),
        (
            """[[people]]
id = "nancy-pelosi"
chamber = "house"
first_name = "Nancy"
last_name = "Pelosi"
state = "California"
""",
            "invalid state code",
        ),
    ],
)
def test_rejects_invalid_person_entries(
    tmp_path: Path, document: str, message: str
) -> None:
    config_path = tmp_path / "tracked_people.toml"
    config_path.write_text(document, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_tracked_people(config_path)


def test_rejects_duplicate_ids(tmp_path: Path) -> None:
    config_path = tmp_path / "tracked_people.toml"
    config_path.write_text(
        """[[people]]
id = "nancy-pelosi"
chamber = "house"
first_name = "Nancy"
last_name = "Pelosi"
state = "CA"

[[people]]
id = "NANCY-PELOSI"
chamber = "house"
first_name = "Nancy"
last_name = "Pelosi"
state = "CA"
""",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="duplicate tracked person id"):
        load_tracked_people(config_path)
