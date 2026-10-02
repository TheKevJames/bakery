"""
File-watch triggers: run a job when files under its `watch` path change,
once they have been quiet for its `debounce_seconds`.

Polls file names, sizes, and modification times rather than subscribing to
filesystem events: the debounce is minutes, and polling is dependency-free
and copes with paths that appear later.
"""

import asyncio
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


class Watcher:
    def __init__(self, fire: Fire) -> None:
        self.fire = fire
        self.tasks: list[asyncio.Task[None]] = []

    def start(self, claws: Iterable[config.Claw]) -> None:
        self.stop()
        for claw in claws:
            if not claw.enabled:
                continue
            for job in claw.jobs:
                if job.watch is not None:
                    self.tasks.append(
                        asyncio.create_task(self._loop(claw, job, job.watch))
                    )

    def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()

    async def _loop(
        self, claw: config.Claw, job: config.Job, path: pathlib.Path
    ) -> None:
        last = await asyncio.to_thread(snapshot, path)
        changed_at: float | None = None
        while True:
            await asyncio.sleep(POLL_SECONDS)
            current = await asyncio.to_thread(snapshot, path)
            if current != last:
                last = current
                changed_at = time.monotonic()
            elif (
                changed_at is not None
                and time.monotonic() - changed_at >= job.debounce_seconds
            ):
                changed_at = None
                log.info(
                    '%s changed: starting %s/%s', path, claw.name, job.name
                )
                self.fire(claw, job)
