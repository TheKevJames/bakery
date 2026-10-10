"""
Blockers: what a `bakery/blocked` task waits on.

Triage ends a blocked task's notes with a list, one blocker per line:

    **Blocked on:**
    - #184 decided
    - #14 done
    - https://github.com/beancount/beangulp/issues/4: closed with total price

Only the description's last such list counts, so later notes supersede it.
Task blockers are checked here; external ones (a URL and its condition) only
by a model, in triage's weekly recheck. A task clears once all its blockers
have.
"""

import argparse
import dataclasses
import json
import re
import subprocess
import sys
from collections.abc import Callable
from typing import Literal

from . import tasks

TAG = 'bakery/blocked'
# A task still here has not been decided, so `#B decided` waits on it.
UNDECIDED = frozenset({'triage', 'bakery/triage', 'bakery/human', TAG})

HEADING = '**Blocked on:**'
TASK_RE = re.compile(r'#(?P<ident>\d+) (?P<until>decided|done)')
EXTERNAL_RE = re.compile(r'(?P<url>https?://\S+): (?P<condition>\S.*)')


class BlockerError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class TaskBlocker:
    ident: int
    until: Literal['decided', 'done']

    def __str__(self) -> str:
        return f'#{self.ident} {self.until}'


@dataclasses.dataclass(frozen=True)
class ExternalBlocker:
    url: str
    condition: str

    def __str__(self) -> str:
        return f'{self.url}: {self.condition}'


Blocker = TaskBlocker | ExternalBlocker


def parse(description: str | None) -> tuple[Blocker, ...]:
    lines = (description or '').splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == HEADING]
    if not starts:
        raise BlockerError(f'no {HEADING} list')
    entries: list[str] = []
    for line in lines[starts[-1] + 1 :]:
        if not line.strip() and not entries:
            continue
        if not line.startswith('- '):
            break
        entries.append(line[2:].strip())
    if not entries:
        raise BlockerError(f'the last {HEADING} list is empty')
    return tuple(_entry(entry) for entry in entries)


def _entry(entry: str) -> Blocker:
    if found := TASK_RE.fullmatch(entry):
        until: Literal['decided', 'done'] = (
            'decided' if found['until'] == 'decided' else 'done'
        )
        return TaskBlocker(int(found['ident']), until)
    if found := EXTERNAL_RE.fullmatch(entry):
        return ExternalBlocker(found['url'], found['condition'])
    raise BlockerError(
        f'{entry!r} is not `#<id> decided`, `#<id> done`, or'
        ' `<url>: <condition>`'
    )


@dataclasses.dataclass(frozen=True)
class Board:
    """The task list, as blockers need it: open tasks' tags, done ids."""

    tags: dict[int, str]
    done: frozenset[int]

    @classmethod
    def load(cls) -> 'Board':
        return cls(
            {int(str(t['id'])): str(t['tag']) for t in tasks.listed()},
            frozenset(int(str(t['id'])) for t in tasks.listed('--done')),
        )

    def state(self, blocker: TaskBlocker) -> str:
        """Its task's tag, or `done`; BlockerError if there is no task."""
        if blocker.ident in self.done:
            return 'done'
        if blocker.ident in self.tags:
            return self.tags[blocker.ident]
        raise BlockerError(f'#{blocker.ident} does not exist')

    def cleared(self, blocker: TaskBlocker) -> bool:
        state = self.state(blocker)
        if blocker.until == 'done':
            return state == 'done'
        return state not in UNDECIDED


def problems(ident: int, description: str | None, board: Board) -> list[str]:
    """Why task `ident` cannot be blocked by its description; [] if it can."""
    try:
        blockers = parse(description)
    except BlockerError as e:
        return [str(e)]
    found: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, TaskBlocker):
            continue
        if blocker.ident == ident:
            found.append(f'{blocker}: a task cannot block itself')
            continue
        try:
            if board.cleared(blocker):
                found.append(
                    f'{blocker}: already cleared ({board.state(blocker)});'
                    ' route on its own merits instead'
                )
        except BlockerError as e:
            found.append(str(e))
    return found


def check(ident: int, append: str = '') -> list[str]:
    """`problems` for task `ident` once `append` is appended to its notes."""
    shown = subprocess.run(
        ('task', 'show', str(ident), '--json'),
        capture_output=True,
        text=True,
        check=False,
    )
    if shown.returncode:
        return [shown.stderr.strip()]
    description = str(json.loads(shown.stdout)['description'] or '')
    # Joined as `task set --description-append` joins it.
    description = '\n\n'.join(p for p in (description, append.strip()) if p)
    return problems(ident, description, Board.load())


def do_check(args: argparse.Namespace) -> int:
    if found := check(args.id, args.append):
        sys.exit(f'cannot block #{args.id}: ' + '; '.join(found))
    return 0


def add_parsers(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    check_ = (
        add_parser('blocked', help=f'{TAG} tasks')
        .add_subparsers(dest='blocked_command')
        .add_parser(
            'check',
            help=f"fail unless a task's {HEADING} list is valid, for task_set",
        )
    )
    check_.add_argument('id', type=int, help='task id')
    check_.add_argument(
        '--append', default='', help='notes about to be appended to it'
    )
    check_.set_defaults(func=do_check)
