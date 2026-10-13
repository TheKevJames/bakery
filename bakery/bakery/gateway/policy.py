"""
A claw's `[policy]`: the paths its pi child may read and write, enforced by
the OS sandbox the child runs in (see sandbox.py); eg. it writes only the
claws' namespace of the task list. Which tools it uses, and how it uses that
namespace, are rules in its instructions, not policy: bash shares pi's
sandbox, so nothing pi's process could refuse would bind bash.
"""

import dataclasses
import json
import pathlib
import re
from collections.abc import Iterable

from . import toml


@dataclasses.dataclass(frozen=True)
class Policy:
    # Absolute paths under $HOME the claw may read; the rest of $HOME is
    # unreadable. Outside $HOME, everything but per-user temp is readable.
    read_paths: tuple[pathlib.Path, ...]
    # Absolute paths or globs the claw may not read, even within read_paths.
    deny_read: tuple[str, ...]
    # Absolute paths the claw may write, besides its own state and profile.
    write_paths: tuple[pathlib.Path, ...]


def _pattern(raw: str, base: pathlib.Path) -> str:
    """Expand like a path, keeping glob characters in the last parts."""
    parts = pathlib.PurePath(raw).parts
    literal = next(
        (i for i, part in enumerate(parts) if re.search(r'[*?[\]{}]', part)),
        len(parts),
    )
    head = toml.expand(str(pathlib.PurePath(*parts[:literal])), base)
    return str(head.joinpath(*parts[literal:]))


def parse(table: toml.Table, profile: pathlib.Path) -> Policy:
    """Relative paths are relative to the claw's profile dir."""
    policy = Policy(
        read_paths=tuple(
            toml.expand(p, profile) for p in table.strings('read_paths')
        ),
        deny_read=tuple(
            _pattern(p, profile) for p in table.strings('deny_read')
        ),
        write_paths=tuple(
            toml.expand(p, profile) for p in table.strings('write_paths')
        ),
    )
    table.done()
    return policy


def serialize(*, secret_names: Iterable[str]) -> str:
    """BAKERY_POLICY, for pi/claw-extensions/policy."""
    return json.dumps({'secret_names': sorted(secret_names)})
