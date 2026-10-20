"""A config reload restarts the watch and cron loops without losing work."""

import asyncio
import dataclasses
import datetime
import pathlib
from collections.abc import Callable

import pytest

from bakery.gateway import config
from bakery.gateway import scheduler
from bakery.gateway import watcher
from testing import gateway as testing_gateway

HOUR = datetime.timedelta(hours=1)


def load(tmp_path: pathlib.Path, claw_toml: str) -> config.Claw:
    """Claw `a`, with only the jobs in `claw_toml` (not eg. dream)."""
    claws = testing_gateway.make_claws(tmp_path, {'a': claw_toml})
    claw = config.load(claws).claw('a')
    jobs = tuple(job for job in claw.jobs if not job.shared)
    return dataclasses.replace(claw, jobs=jobs)


def reload(claw: config.Claw, name: str, **changes: object) -> config.Claw:
    """`claw` as a reload loads it: new objects, job `name` changed."""
    job = dataclasses.replace(claw.job(name), **changes)
    jobs = tuple(job if j.name == name else j for j in claw.jobs)
    return dataclasses.replace(claw, jobs=jobs)


async def until(condition: Callable[[], object]) -> None:
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


def test_reload_keeps_a_change_still_being_debounced(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(watcher, 'POLL_SECONDS', 0.01)
    dirs = {name: tmp_path / name for name in ('pending', 'quiet', 'moved')}
    for path in dirs.values():
        path.mkdir()
        (path / 'f.md').write_text('0')
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    claw = load(
        tmp_path,
        ''.join(
            f'[[job]]\nname = "{name}"\nprompt = "p"\nwatch = "{path}"\n'
            'debounce_seconds = 3600\n'
            for name, path in dirs.items()
        ),
    )
    fired: list[str] = []

    async def scenario() -> None:
        watch = watcher.Watcher(lambda _, job: fired.append(job.name))
        watch.start([claw])
        await asyncio.sleep(0.3)  # baseline snapshots
        (dirs['pending'] / 'f.md').write_text('changed')
        (dirs['moved'] / 'f.md').write_text('changed')
        await asyncio.sleep(0.3)  # changes seen, debouncing
        # Reloaded mid-debounce, with every job now quiet long enough, and
        # `moved` watching somewhere else.
        reloaded = claw
        for name in dirs:
            reloaded = reload(reloaded, name, debounce_seconds=0)
        reloaded = reload(reloaded, 'moved', watch=elsewhere)
        watch.start([reloaded])
        await until(lambda: fired)
        await asyncio.sleep(0.3)  # and nothing else fires
        watch.stop()

    asyncio.run(scenario())
    assert fired == ['pending']


def test_reload_keeps_a_fire_that_came_due(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    claw = load(
        tmp_path,
        '[[job]]\nname = "kept"\ncron = "0 7 * * *"\nprompt = "p"\n'
        '[[job]]\nname = "changed"\ncron = "0 7 * * *"\nprompt = "p"\n',
    )
    start = datetime.datetime.now(datetime.UTC)
    reloaded = False

    def next_fire(cron: str, after: datetime.datetime) -> datetime.datetime:
        del cron, after
        # Before the reload, both jobs were due 10s ago (within the grace).
        return start + HOUR if reloaded else start - HOUR / 360

    async def sleep_until(when: datetime.datetime) -> None:
        # Loops from before the reload never woke (event loop lag, or a Mac
        # just awake), though their fire came due.
        if not reloaded or when > datetime.datetime.now(datetime.UTC):
            await asyncio.Event().wait()

    monkeypatch.setattr(scheduler, 'next_fire', next_fire)
    monkeypatch.setattr(scheduler, '_sleep_until', sleep_until)
    fired: list[tuple[str, datetime.datetime]] = []

    async def scenario() -> None:
        nonlocal reloaded
        cron = scheduler.Scheduler(
            lambda _, job, due: fired.append((job.name, due))
        )
        cron.start([claw], datetime.UTC)
        await asyncio.sleep(0.1)
        reloaded = True
        cron.start([reload(claw, 'changed', cron='0 8 * * *')], datetime.UTC)
        await until(lambda: fired)
        await asyncio.sleep(0.1)  # and nothing else fires
        # `status` still reports what is next.
        assert cron.upcoming == {
            ('a', 'kept'): start + HOUR,
            ('a', 'changed'): start + HOUR,
        }
        cron.stop()

    asyncio.run(scenario())
    assert fired == [('kept', start - HOUR / 360)]
