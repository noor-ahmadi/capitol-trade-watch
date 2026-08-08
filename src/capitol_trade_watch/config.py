"""Configuration loading and validation."""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from capitol_trade_watch.models import TrackedPerson

_PERSON_KEYS = frozenset({"id", "chamber", "first_name", "last_name", "state"})
_STATE_CODE = re.compile(r"^[A-Z]{2}$")
_SUPPORTED_CHAMBERS = frozenset({"house"})


class ConfigError(ValueError):
    """Raised when the tracker configuration is missing or invalid."""


def load_tracked_people(path: str | Path) -> tuple[TrackedPerson, ...]:
    """Load and validate tracked people from a TOML configuration file."""
    config_path = Path(path)
    try:
        with config_path.open("rb") as config_file:
            document = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise ConfigError(f"configuration file not found: {config_path}") from error
    except OSError as error:
        raise ConfigError(f"could not read configuration file: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"invalid TOML in {config_path}: {error}") from error

    people_data = document.get("people")
    if not isinstance(people_data, list) or not people_data:
        raise ConfigError("configuration must define at least one [[people]] table")

    people: list[TrackedPerson] = []
    seen_ids: set[str] = set()
    for index, item in enumerate(people_data, start=1):
        if not isinstance(item, Mapping):
            raise ConfigError(f"people entry {index} must be a TOML table")

        unknown_keys = set(item) - _PERSON_KEYS
        if unknown_keys:
            names = ", ".join(sorted(unknown_keys))
            raise ConfigError(f"people entry {index} has unknown field(s): {names}")

        person_id = _required_string(item, "id", index)
        chamber = _required_string(item, "chamber", index).casefold()
        first_name = _required_string(item, "first_name", index)
        last_name = _required_string(item, "last_name", index)
        state = _required_string(item, "state", index).upper()

        normalized_id = person_id.casefold()
        if normalized_id in seen_ids:
            raise ConfigError(f"duplicate tracked person id: {person_id}")
        if chamber not in _SUPPORTED_CHAMBERS:
            raise ConfigError(
                f"unsupported chamber for {person_id}: {chamber}; supported: house"
            )
        if not _STATE_CODE.fullmatch(state):
            raise ConfigError(
                f"invalid state code for {person_id}: {state!r}; expected two letters"
            )

        seen_ids.add(normalized_id)
        people.append(
            TrackedPerson(
                id=person_id,
                chamber=chamber,
                first_name=first_name,
                last_name=last_name,
                state=state,
            )
        )

    return tuple(people)


def validate_config(path: str | Path) -> tuple[TrackedPerson, ...]:
    """Validate configuration and return its normalized tracked people."""
    return load_tracked_people(path)


def _required_string(item: Mapping[str, Any], key: str, index: int) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"people entry {index} requires a non-empty {key}")
    return value.strip()
