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
import re
import sys
from collections.abc import Callable
from typing import Literal

from ..gateway import policy
from . import tasks

TAG = 'bakery/blocked'
DONE = 'done'
# A task still here has not been decided, so `#B decided` waits on it.
# The operator's own tasks record no decisions, so only `#B done` may wait on
# them.
UNDECIDED = frozenset({'bakery/triage', 'bakery/human', TAG})

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
    """The task list, as blockers need it: open tasks' tags."""

    tags: dict[int, str]

    @classmethod
    def load(cls) -> 'Board':
        return cls({int(str(t['id'])): str(t['tag']) for t in tasks.listed()})

    def state(self, blocker: TaskBlocker) -> str:
        """Its task's tag, or DONE once there is no such task."""
        # Done tasks are deleted, so a typo also reads as done; `problems`
        # refuses it as already cleared.
        return self.tags.get(blocker.ident, DONE)

    def cleared(self, blocker: TaskBlocker) -> bool:
        state = self.state(blocker)
        if blocker.until == 'done':
            return state == DONE
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
        state = board.state(blocker)
        if not (DONE in (blocker.until, state) or policy.in_namespace(state)):
            found.append(
                f"{blocker}: #{blocker.ident} is the operator's ({state}),"
                f' so use `#{blocker.ident} done`'
            )
        elif board.cleared(blocker):
            if state == DONE:
                state = f'#{blocker.ident} is done or does not exist'
            found.append(
                f'{blocker}: already cleared ({state});'
                ' route on its own merits instead'
            )
    return found


def check(ident: int, append: str = '') -> list[str]:
    """`problems` for task `ident` once `append` is appended to its notes."""
    try:
        description = str(tasks.shown(ident)['description'] or '')
    except tasks.TaskError as e:
        return [str(e)]
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
