"""Strict access to TOML tables: typed getters, and unknown keys are errors."""

import os
import pathlib
import re
from collections.abc import Mapping
from typing import Any

ENV_NAME_RE = re.compile(r'[A-Z_][A-Z0-9_]*')


class ConfigError(Exception):
    pass


class Table:
    """Typed, strict access to one TOML table; reports unused keys."""

    def __init__(self, data: Mapping[str, Any], where: str) -> None:
        self.data = data
        self.where = where
        self.used: set[str] = set()

    def _get(self, key: str) -> object:
        self.used.add(key)
        if key not in self.data:
            raise ConfigError(f'{self.where}: missing {key}')
        return self.data[key]

    def _wrong_type(self, key: str) -> ConfigError:
        return ConfigError(f'{self.where}.{key}: wrong type')

    def string(self, key: str, choices: tuple[str, ...] = ()) -> str:
        value = self._get(key)
        if not isinstance(value, str):
            raise self._wrong_type(key)
        if choices and value not in choices:
            raise ConfigError(
                f'{self.where}.{key}: must be one of {", ".join(choices)}'
            )
        return value

    def boolean(self, key: str) -> bool:
        value = self._get(key)
        if not isinstance(value, bool):
            raise self._wrong_type(key)
        return value

    def positive(self, key: str) -> float:
        value = self._get(key)
        # bool is an int subclass; `true` is not a number here.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self._wrong_type(key)
        if value <= 0:
            raise ConfigError(f'{self.where}.{key}: must be positive')
        return float(value)

    def count(self, key: str, *, minimum: int = 1) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._wrong_type(key)
        if value < minimum:
            raise ConfigError(f'{self.where}.{key}: must be >= {minimum}')
        return value

    def strings(self, key: str) -> tuple[str, ...]:
        value = self._get(key)
        if not isinstance(value, list) or not all(
            isinstance(x, str) for x in value
        ):
            raise ConfigError(f'{self.where}.{key}: must be a list of strings')
        return tuple(str(x) for x in value)

    def secrets(self, key: str) -> dict[str, str]:
        """
        Secrets as `NAME` or `NAME=ITEM` entries: environment variable NAME,
        valued from the secret ITEM (default: NAME).
        """
        result: dict[str, str] = {}
        for entry in self.strings(key):
            name, _, item = entry.partition('=')
            item = item or name
            if not (
                ENV_NAME_RE.fullmatch(name) and ENV_NAME_RE.fullmatch(item)
            ):
                raise ConfigError(
                    f'{self.where}.{key}: {entry!r} is not NAME or NAME=ITEM'
                )
            result[name] = item
        return result

    def table(self, key: str) -> 'Table':
        value = self._get(key)
        if not isinstance(value, dict):
            raise self._wrong_type(key)
        return Table(value, f'{self.where}.{key}')

    def optional(self, key: str) -> bool:
        return key in self.data

    def done(self) -> None:
        unknown = set(self.data) - self.used
        if unknown:
            raise ConfigError(
                f'{self.where}: unknown keys {", ".join(sorted(unknown))}'
            )


def expand(raw: str, base: pathlib.Path) -> pathlib.Path:
    def env(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1))
        if not value:
            raise ConfigError(f'${{{match.group(1)}}} is not set (in {raw})')
        return value

    expanded = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', env, raw)
    return (base / pathlib.Path(expanded).expanduser()).resolve()
