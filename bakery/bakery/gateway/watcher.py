"""
File-watch triggers: run a job when files under its `watch` path change,
once they have been quiet for its `debounce_seconds`.

Polls file names, sizes, and modification times rather than subscribing to
filesystem events: the debounce is minutes, and polling is dependency-free
and copes with paths that appear later.
"""

import asyncio
import dataclasses
import logging
import pathlib
import time
from collections.abc import Callable
from collections.abc import Iterable

from . import config

POLL_SECONDS = 10.0

log = logging.getLogger(__name__)

Fire = Callable[[config.Claw, config.Job], object]
Snapshot = frozenset[tuple[str, int, int]]


def snapshot(path: pathlib.Path) -> Snapshot:
    """Every file under `path` (or `path` itself): name, size, mtime."""
    files = [path] if path.is_file() else path.rglob('*')
    entries = set()
    for file in files:
        try:
            stat = file.stat()
        except OSError:  # removed while listing
            continue
        if file.is_file():
            entries.add((str(file), stat.st_size, stat.st_mtime_ns))
    return frozenset(entries)


@dataclasses.dataclass
class _State:
    """A watch's progress, kept across reloads (see `Watcher.start`)."""

    path: pathlib.Path
    # None until the first snapshot.
    last: Snapshot | None = None
    # When it last changed (time.monotonic), if not yet quiet long enough.
    changed_at: float | None = None


class Watcher:
    def __init__(self, fire: Fire) -> None:
        self.fire = fire
        self.tasks: list[asyncio.Task[None]] = []
        self.states: dict[tuple[str, str], _State] = {}

    def start(self, claws: Iterable[config.Claw]) -> None:
        """
        (Re)start a loop per watch job. A watch whose path is unchanged keeps
        its state, so a reload neither drops a change still being debounced
        nor takes it as the new baseline.
        """
        self._cancel()
        states = {}
        for claw in claws:
            if not claw.enabled:
                continue
            for job in claw.jobs:
                if job.watch is None:
                    continue
                state = self.states.get((claw.name, job.name))
                if state is None or state.path != job.watch:
                    state = _State(job.watch)
                states[claw.name, job.name] = state
                self.tasks.append(
                    asyncio.create_task(self._loop(claw, job, state))
                )
        self.states = states

    def stop(self) -> None:
        self._cancel()
        self.states.clear()

    def _cancel(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()

    async def _loop(
        self, claw: config.Claw, job: config.Job, state: _State
    ) -> None:
        # A cancelled loop never resumes past an await, so only the live loop
        # updates its state.
        if state.last is None:
            state.last = await asyncio.to_thread(snapshot, state.path)
        while True:
            await asyncio.sleep(POLL_SECONDS)
            current = await asyncio.to_thread(snapshot, state.path)
            if current != state.last:
                state.last = current
                state.changed_at = time.monotonic()
            elif (
                state.changed_at is not None
                and time.monotonic() - state.changed_at >= job.debounce_seconds
            ):
                state.changed_at = None
                log.info(
                    '%s changed: starting %s/%s',
                    state.path,
                    claw.name,
                    job.name,
                )
                self.fire(claw, job)
