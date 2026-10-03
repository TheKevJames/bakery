import asyncio
import json
import pathlib
import subprocess

import pytest

from bakery.gateway import config
from bakery.gateway import core
from bakery.gateway import watcher
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply
QUEUE = """
[policy]
tools = ["task_list", "task_show", "task_set"]
[[job]]
name = "queue"
cron = "0 * * * *"
prompt = "Triage this ticket."
collector = "triage"
repeat = true
"""


def task(*args: str) -> str:
    return subprocess.run(
        ('task', *args), capture_output=True, text=True, check=True
    ).stdout


def show(ident: int) -> dict[str, object]:
    shown: dict[str, object] = json.loads(task('show', str(ident), '--json'))
    return shown


def call(**args: object) -> harness.Reply:
    return Reply(tool='task_set', args=args)


@pytest.fixture(name='tasks', scope='function')
def fixture_tasks(
    root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    folder = root / 'tasks'
    folder.mkdir()
    monkeypatch.setenv('TASK_FOLDER', str(folder))
    task('add', 'from scout', '--tag', 'bakery/triage')
    task('add', 'recurring', '--next', '2030-01-01')
    task('add', 'from kevin')
    task('add', 'already routed', '--tag', 'bakery/build')
    return folder


def triage_claws(root: pathlib.Path) -> pathlib.Path:
    return testing_gateway.make_claws(
        root,
        {'triage': QUEUE},
        extensions={'triage': ['pi/claw-extensions/task.ts']},
    )


def test_works_through_the_queue_one_ticket_per_unit(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    # Kevin's inbox goes first, though scout's ticket has the lower id.
    fake_llm.queue(
        call(id=3, claim=True),
        call(id=3, priority='high', size='small', description_append='notes'),
        call(id=3, tag='bakery/build', release=True),
        Reply(text='3 to build'),
        call(id=1, claim=True),
        call(id=1, tag='bakery/wontfix', priority='low', size='small'),
        call(id=1, release=True),
        Reply(text='1 to wontfix'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        first = await fake.wait('triage/task-3')
        assert (first.status, first.final_text) == ('settled', '3 to build')
        second = await fake.wait('triage/task-1')
        assert (second.status, second.final_text) == (
            'settled',
            '1 to wontfix',
        )
        # The repeat after it finds nothing left (#2 is scheduled).
        await asyncio.sleep(0.5)
        assert not gateway.active and not gateway.queue

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    assert json.dumps('<ticket id="3" waiting="1">')[1:-1] in (
        fake_llm.transcript(0)
    )
    first, second = show(3), show(1)
    assert (
        first['tag'],
        first['priority'],
        first['size'],
        first['owner'],
    ) == ('bakery/build', 'high', 'small', None)
    assert first['description'] == 'notes'
    assert (second['tag'], second['priority'], second['owner']) == (
        'bakery/wontfix',
        'low',
        None,
    )
    assert show(2)['tag'] == 'triage'  # scheduled tasks are not triaged
    assert len(fake_llm.requests) == 8


def test_a_ticket_left_waiting_is_not_repeated(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    fake_llm.queue(Reply(text='forgot to route it'))
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        await fake.wait('triage/task-3')
        async with asyncio.timeout(10):
            while not any('still waiting' in n for n in fake.notices):
                await asyncio.sleep(0.05)

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    assert len(fake_llm.requests) == 1


def test_file_changes_trigger_after_a_quiet_period(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(watcher, 'POLL_SECONDS', 0.05)
    watched = root / 'watched'
    (watched / 'sub').mkdir(parents=True)
    claws = testing_gateway.make_claws(
        root,
        {
            'a': f"""
                [[job]]
                name = "react"
                cron = "0 0 1 1 *"
                prompt = "Something changed."
                watch = "{watched}"
                debounce_seconds = 0.5
            """
        },
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        del gateway
        await asyncio.sleep(0.2)
        assert not fake_llm.requests  # nothing fires at startup
        for i in range(4):  # a burst of changes...
            (watched / 'sub' / f'{i}.md').write_text(str(i))
            await asyncio.sleep(0.1)
        assert not fake.finished
        async with asyncio.timeout(10):
            while not fake.finished:
                await asyncio.sleep(0.05)
        await asyncio.sleep(0.5)  # ...is one run

    testing_gateway.run_gateway(claws, fake, scenario)
    assert [r.trigger for r in fake.finished] == ['watch:react']
    assert len(fake_llm.requests) == 1


def test_job_queue_options(tmp_path: pathlib.Path) -> None:
    claws = testing_gateway.make_claws(
        tmp_path,
        {
            'a': """
                [[job]]
                name = "q"
                cron = "0 * * * *"
                prompt = "p"
                collector = "triage"
                repeat = true
                watch = "~/tasks"
            """
        },
    )

    job = config.load(claws).claw('a').job('q')

    assert (job.repeat, job.watch, job.debounce_seconds) == (
        True,
        pathlib.Path.home() / 'tasks',
        120.0,
    )
