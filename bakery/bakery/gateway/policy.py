"""
A claw's `[policy]`: what its pi child may do, enforced in the child by
pi/claw-extensions/policy (tool and path checks, plus an OS sandbox around
bash). The gateway serializes it into BAKERY_POLICY.
"""

import dataclasses
import json
import pathlib
import re
from collections.abc import Iterable

from . import toml

DOMAIN_RE = re.compile(r'(\*\.)?[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)*(:\d+)?')


@dataclasses.dataclass(frozen=True)
class Policy:
    # Tool names; `*` matches any characters (eg. `mcp__context7__*`).
    tools: tuple[str, ...]
    # Absolute paths the claw may write (bash and edit/write). $TMPDIR is
    # always writable.
    write_paths: tuple[pathlib.Path, ...]
    # Absolute paths or globs the claw may not read.
    deny_read: tuple[str, ...]
    # Domains bash may reach; empty means no network.
    network: tuple[str, ...]
    # Regexes; a matching tool name or bash command needs my approval.
    confirm: tuple[str, ...]


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
    policy = Policy(
        tools=table.strings('tools'),
        write_paths=tuple(
            toml.expand(p, profile) for p in table.strings('write_paths')
        ),
        deny_read=tuple(
            _pattern(p, profile) for p in table.strings('deny_read')
        ),
        network=network,
        confirm=confirm,
    )
    table.done()
    return policy


def serialize(
    policy: Policy,
    *,
    extra_deny_read: Iterable[pathlib.Path],
    allow_read: Iterable[pathlib.Path],
    secret_names: Iterable[str],
) -> str:
    return json.dumps(
        {
            'tools': policy.tools,
            'write_paths': [str(p) for p in policy.write_paths],
            'deny_read': [
                *policy.deny_read,
                *(str(p) for p in extra_deny_read),
            ],
            'allow_read': [str(p) for p in allow_read],
            'network': policy.network,
            'confirm': policy.confirm,
            'secret_names': sorted(secret_names),
        }
    )
