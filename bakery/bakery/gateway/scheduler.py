"""
Cron jobs. Missed fires (gateway down, Mac asleep) are skipped, not caught
up: a job only runs if it wakes up within MISSED_GRACE of its fire time.
"""

import asyncio
import datetime
import logging
from collections.abc import Callable
from collections.abc import Iterable

import croniter

from . import config

MISSED_GRACE = datetime.timedelta(seconds=60)
# asyncio sleeps on a monotonic clock that stops while macOS sleeps, so sleep
# in short steps and re-check the wall clock to notice a wake-up promptly.
SLEEP_STEP = 30.0

log = logging.getLogger(__name__)

Fire = Callable[[config.Claw, config.Job, datetime.datetime], None]


def next_fire(job: config.Job, after: datetime.datetime) -> datetime.datetime:
    fire: datetime.datetime = croniter.croniter(job.cron, after).get_next(
        datetime.datetime
    )
    return fire


def should_run(
    job: config.Job, due: datetime.datetime, now: datetime.datetime
) -> bool:
    if now - due > MISSED_GRACE:
        return False
    return job.active_hours is None or job.active_hours.contains(due.time())


def work_key(
    claw: config.Claw, job: config.Job, due: datetime.datetime
) -> str:
    return f'{claw.name}/{job.name}-{due:%Y%m%d-%H%M}'


async def _sleep_until(when: datetime.datetime) -> None:
    while True:
        remaining = (when - datetime.datetime.now(when.tzinfo)).total_seconds()
        if remaining <= 0:
            return
        await asyncio.sleep(min(remaining, SLEEP_STEP))


class Scheduler:
    def __init__(self, fire: Fire) -> None:
        self.fire = fire
        self.tasks: list[asyncio.Task[None]] = []
        self.upcoming: dict[tuple[str, str], datetime.datetime] = {}

    def start(self, claws: Iterable[config.Claw], tz: datetime.tzinfo) -> None:
        self.stop()
        for claw in claws:
            if not claw.enabled:
                continue
            for job in claw.jobs:
                self.tasks.append(
                    asyncio.create_task(self._loop(claw, job, tz))
                )

    def stop(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.tasks.clear()
        self.upcoming.clear()

    async def _loop(
        self, claw: config.Claw, job: config.Job, tz: datetime.tzinfo
    ) -> None:
        while True:
            due = next_fire(job, datetime.datetime.now(tz))
            self.upcoming[claw.name, job.name] = due
            await _sleep_until(due)
            now = datetime.datetime.now(tz)
            if should_run(job, due, now):
                self.fire(claw, job, due)
            else:
                log.info('skipping %s/%s due %s', claw.name, job.name, due)
