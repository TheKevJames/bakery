"""
Starting jobs: building their requests, and running their collectors first.

Cron, file watches, repeats, and manual triggers all start a job the same
way. A job with a collector only runs if the collector finds something, and
only one collection or run per claw and job happens at a time, so the same
input is never handed over twice.
"""

import asyncio
import dataclasses
import datetime
import logging
from collections.abc import Callable

from .. import collectors
from . import config

log = logging.getLogger(__name__)


class JobError(Exception):
    """A manual trigger that cannot start."""


@dataclasses.dataclass(frozen=True)
class Request:
    claw: str
    work_key: str
    prompt: str
    trigger: str
    # The job that started the unit of work; its later runs inherit it.
    job: str | None = None


def stamp() -> str:
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')


class Jobs:
    def __init__(
        self,
        submit: Callable[[Request], None],
        notify: Callable[[str], None],
        busy: Callable[[str, str], bool],
    ) -> None:
        self.submit = submit
        self.notify = notify
        # Whether a run of (claw, job) is queued or active.
        self.busy = busy
        # (claw, job) -> the collection under way, by its unit's work key.
        self._collections: dict[
            tuple[str, str], tuple[str, asyncio.Task[None]]
        ] = {}

    def start(
        self,
        claw: config.Claw,
        job: config.Job,
        kind: str,
        *,
        work_key: str | None = None,
        prompt: str | None = None,
        after: str | None = None,
    ) -> str:
        """
        Start `job` (`kind`: cron, watch, repeat, or manual); returns the
        work key, which a collector may still replace.

        Only manual starts raise (JobError) when the job is already busy;
        the others are skipped quietly. `after` is the unit a repeat follows:
        if the collector hands it back again, its run did not get anywhere,
        and repeating would loop.
        """
        manual = kind == 'manual'
        request = Request(
            claw.name,
            work_key or f'{claw.name}/{job.name}-{stamp()}',
            prompt or job.prompt,
            f'{kind}:{job.name}',
            job.name,
        )
        if job.collector is None:
            self.submit(request)
            return request.work_key
        key = (claw.name, job.name)
        if key in self._collections or self.busy(*key):
            if manual:
                raise JobError(f'{claw.name}/{job.name} is already running')
            return request.work_key
        task = asyncio.create_task(
            self._collect(claw, job, request, manual, after)
        )
        self._collections[key] = (request.work_key, task)
        task.add_done_callback(lambda _: self._collections.pop(key, None))
        return request.work_key

    def collecting(self, work_key: str) -> bool:
        """Whether `work_key` is waiting on its job's collector."""
        return any(key == work_key for key, _ in self._collections.values())

    def collecting_for(self, claw: str) -> list[str]:
        return [
            work_key
            for (name, _), (work_key, _) in self._collections.items()
            if name == claw
        ]

    def stop(self) -> None:
        for _, task in list(self._collections.values()):
            task.cancel()

    async def _collect(
        self,
        claw: config.Claw,
        job: config.Job,
        request: Request,
        manual: bool,
        after: str | None,
    ) -> None:
        assert job.collector is not None
        collector = collectors.COLLECTORS[job.collector]
        name = f'{claw.name}/{job.name}'
        try:
            collection = await asyncio.to_thread(collector, claw)
        except Exception as e:
            log.exception('collecting for %s failed', name)
            self.notify(f'{name}: collecting failed: {e}')
            return
        if collection.errors:
            self.notify(
                f'{name} collection problems:\n' + '\n'.join(collection.errors)
            )
        if collection.text is None:
            log.info('%s: nothing new', name)
            if manual:
                self.notify(f'{name}: nothing new')
            return
        if after is not None and collection.work_key == after:
            self.notify(
                f'{name}: {after} settled but is still waiting; stopping'
                ' here rather than repeating it'
            )
            return
        self.submit(
            dataclasses.replace(
                request,
                prompt=f'{request.prompt}\n\n{collection.text}',
                work_key=collection.work_key or request.work_key,
            )
        )
