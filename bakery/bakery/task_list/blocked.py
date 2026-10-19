"""
Blockers: what a `blocked` task, in the claws' namespace, waits on.

Triage ends a blocked task's notes with a list, one blocker per line, naming
tasks by `<namespace>:<id>`:

    **Blocked on:**
    - #bakery:184 decided
    - #default:14 done
    - https://github.com/beancount/beangulp/issues/4: closed with total price

Only the description's last such list counts, so later notes supersede it.
Task blockers are checked here; external ones (a URL and its condition) only
by a model, in triage's weekly recheck. A task clears once all its blockers
have.
"""

import dataclasses
import re
from typing import Literal

from . import store

TAG = 'blocked'
DONE = 'done'
# A claws' task still here has not been decided, so `#B decided` waits on it.
# The operator's own tasks record no decisions, so only `#B done` may wait on
# them.
UNDECIDED = frozenset({'triage', 'human', TAG})

HEADING = '**Blocked on:**'
TASK_RE = re.compile(
    r'#(?P<namespace>[a-z0-9][a-z0-9_.-]*):(?P<ident>\d+)'
    r' (?P<until>decided|done)'
)
EXTERNAL_RE = re.compile(r'(?P<url>https?://\S+): (?P<condition>\S.*)')


class BlockerError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class TaskBlocker:
    namespace: str
    ident: int
    until: Literal['decided', 'done']

    @property
    def ref(self) -> str:
        return f'{self.namespace}:{self.ident}'

    def __str__(self) -> str:
        return f'#{self.ref} {self.until}'


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
        return TaskBlocker(found['namespace'], int(found['ident']), until)
    if found := EXTERNAL_RE.fullmatch(entry):
        return ExternalBlocker(found['url'], found['condition'])
    raise BlockerError(
        f'{entry!r} is not `#<namespace>:<id> decided`,'
        ' `#<namespace>:<id> done`, or `<url>: <condition>`'
    )


@dataclasses.dataclass(frozen=True)
class Board:
    """The task list, as blockers need it: open tasks' tags, by ref."""

    tags: dict[str, str]

    @classmethod
    def load(cls) -> 'Board':
        return cls({store.ref(t): str(t['tag']) for t in store.listed()})

    def state(self, blocker: TaskBlocker) -> str:
        """Its task's tag, or DONE once there is no such task."""
        # Done tasks are deleted, so a typo also reads as done; `problems`
        # refuses it as already cleared.
        return self.tags.get(blocker.ref, DONE)

    def cleared(self, blocker: TaskBlocker) -> bool:
        state = self.state(blocker)
        if blocker.until == 'done':
            return state == DONE
        return blocker.namespace != store.NAMESPACE or state not in UNDECIDED


def problems(
    ref: str | None, description: str | None, board: Board
) -> list[str]:
    """
    Why a claws' task (`ref`, or None if it is new) cannot be blocked by its
    description; [] if it can.
    """
    try:
        blockers = parse(description)
    except BlockerError as e:
        return [str(e)]
    found: list[str] = []
    for blocker in blockers:
        if not isinstance(blocker, TaskBlocker):
            continue
        if blocker.ref == ref:
            found.append(f'{blocker}: a task cannot block itself')
            continue
        state = board.state(blocker)
        if not (
            DONE in (blocker.until, state)
            or blocker.namespace == store.NAMESPACE
        ):
            found.append(
                f"{blocker}: #{blocker.ref} is the operator's ({state}),"
                f' so use `#{blocker.ref} done`'
            )
        elif board.cleared(blocker):
            if state == DONE:
                state = f'#{blocker.ref} is done or does not exist'
            found.append(
                f'{blocker}: already cleared ({state});'
                ' route on its own merits instead'
            )
    return found
