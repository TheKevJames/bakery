"""
A claw's `[policy]`: what its pi child may do. Paths and network are enforced
by the OS sandbox the child runs in (see sandbox.py); tools and confirmations
by pi/claw-extensions/policy in the child, to which the gateway passes them
as BAKERY_POLICY.
"""

import dataclasses
import json
import pathlib
import re
from collections.abc import Iterable

from . import toml

DOMAIN_RE = re.compile(r'(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d+)?')
TAG_RE = re.compile(r'[a-z0-9][a-z0-9_.-]*(/[a-z0-9][a-z0-9_.-]*)*')
# Claws only change tasks tagged under this section; the rest are Kevin's.
NAMESPACE = 'bakery'


def in_namespace(tag: str) -> bool:
    return tag.startswith(f'{NAMESPACE}/')


@dataclasses.dataclass(frozen=True)
class Policy:
    # Tool names; `*` matches any characters (eg. `task_*`).
    tools: tuple[str, ...]
    # Absolute paths under $HOME the claw may read; the rest of $HOME is
    # unreadable. Outside $HOME, everything but per-user temp is readable.
    read_paths: tuple[pathlib.Path, ...]
    # Absolute paths or globs the claw may not read, even within read_paths.
    deny_read: tuple[str, ...]
    # Absolute paths the claw may write, besides its own state and profile.
    write_paths: tuple[pathlib.Path, ...]
    # Domains the claw (pi and bash alike) may reach.
    network: tuple[str, ...]
    # Regexes; a matching tool name or bash command needs my approval.
    confirm: tuple[str, ...]
    # The task list section `task_add` files into, under NAMESPACE.
    task_tag: str


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
    confirm = table.strings('confirm')
    for pattern in confirm:
        try:
            re.compile(pattern)
        except re.error as e:
            raise toml.ConfigError(
                f'{table.where}.confirm: bad regex {pattern!r}: {e}'
            ) from None
    network = table.strings('network')
    for domain in network:
        if not DOMAIN_RE.fullmatch(domain):
            raise toml.ConfigError(
                f'{table.where}.network: {domain!r} is not a domain'
            )
    task_tag = table.string('task_tag')
    if not TAG_RE.fullmatch(task_tag):
        raise toml.ConfigError(
            f'{table.where}.task_tag: {task_tag!r} is not a lowercase tag path'
        )
    if not in_namespace(task_tag):
        raise toml.ConfigError(
            f'{table.where}.task_tag: {task_tag!r} is not under {NAMESPACE}/'
        )
    policy = Policy(
        tools=table.strings('tools'),
        read_paths=tuple(
            toml.expand(p, profile) for p in table.strings('read_paths')
        ),
        deny_read=tuple(
            _pattern(p, profile) for p in table.strings('deny_read')
        ),
        write_paths=tuple(
            toml.expand(p, profile) for p in table.strings('write_paths')
        ),
        network=network,
        confirm=confirm,
        task_tag=task_tag,
    )
    table.done()
    return policy


def serialize(policy: Policy, *, secret_names: Iterable[str]) -> str:
    return json.dumps(
        {
            'tools': policy.tools,
            'confirm': policy.confirm,
            'secret_names': sorted(secret_names),
        }
    )
