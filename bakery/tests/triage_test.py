import asyncio
import json
import pathlib
import shlex

import pytest
from task import actors
from task import ops
from task import refs

from bakery.collectors import triage
from bakery.gateway import core
from bakery.gateway import watcher
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks

QUEUE = """
[[job]]
name = "queue"
cron = "0 * * * *"
prompt = "Triage this task."
collector = "triage"
repeat = true
"""
RECHECK = """
[[job]]
name = "recheck"
cron = "0 6 * * 1"
prompt = "Recheck these blocked tasks."
collector = "triage-recheck"
"""


def task(*args: str) -> harness.Reply:
    """The claw running `task` in bash."""
    return harness.call('bash', command=shlex.join(('task', *args)))


@pytest.fixture(name='tasks', scope='function')
def fixture_tasks(root: pathlib.Path) -> pathlib.Path:
    folder = root / 'tasks'
    testing_tasks.task('add', 'from scout', '-n', 'bakery', '--tag', 'triage')
    testing_tasks.task(
        'add', 'recurring', '-n', 'bakery', '--tag', 'triage',
        '--next', '2030-01-01',
    )  # fmt: skip
    testing_tasks.task(
        'add', 'already routed', '-n', 'bakery', '--tag', 'build'
    )
    testing_tasks.task('add', 'from the operator', '--tag', 'dev')
    return folder


def prompt(fake_llm: harness.FakeLLM, index: int) -> str:
    messages = fake_llm.requests[index]['messages']
    assert isinstance(messages, list)
    content = next(m for m in messages if m['role'] == 'user')['content']
    assert isinstance(content, list)
    return ''.join(str(part['text']) for part in content)


def triage_claws(root: pathlib.Path, claw_toml: str = QUEUE) -> pathlib.Path:
    return testing_gateway.make_claws(
        root, {'triage': claw_toml}, rules={'triage': 'triage'}
    )


def test_works_through_the_queue_one_task_per_unit(
    root: pathlib.Path,
    tasks: pathlib.Path,
    fake_llm: harness.FakeLLM,
    logs: testing_gateway.LogWatch,
) -> None:
    del tasks
    testing_tasks.task('add', 'handed over by the operator', '-n', 'bakery')
    fake_llm.queue(
        task('bakery:1', 'set', '--priority', 'high', '--size', 'small',
             '--comment', 'notes'),
        task('bakery:1', 'set', '--tag', 'build'),
        harness.Reply(text='1 to build'),
        task('bakery:4', 'set', '--tag', 'wontfix', '--priority', 'low'),
        harness.Reply(text='4 to wontfix'),
    )  # fmt: skip
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        first = await fake.wait('triage/task-1')
        assert (first.status, first.final_text) == ('settled', '1 to build')
        second = await fake.wait('triage/task-4')
        assert (second.status, second.final_text) == (
            'settled',
            '4 to wontfix',
        )
        # The repeat after it finds nothing left: bakery:2 is scheduled, and
        # the operator's own triage is not the claws'.
        await asyncio.to_thread(logs.wait_for, 'triage/queue: nothing new')

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    assert json.dumps('<task id="1" waiting="1">')[1:-1] in (
        fake_llm.transcript(0)
    )
    first, second = (
        testing_tasks.show('bakery:1'),
        testing_tasks.show('bakery:4'),
    )
    # Claimed for each run by the collector, and released when it ended.
    assert (
        first['tag'],
        first['priority'],
        first['size'],
        first['claim'],
    ) == ('build', 'high', 'small', None)
    assert testing_tasks.said('bakery:1') == ['notes']
    assert (second['tag'], second['priority'], second['claim']) == (
        'wontfix',
        'low',
        None,
    )
    assert (
        testing_tasks.show('bakery:2')['tag'] == 'triage'
    )  # scheduled: not triaged
    assert (
        testing_tasks.show('1')['tag'],
        testing_tasks.show('1')['claim'],
    ) == ('dev', None)
    assert len(fake_llm.requests) == 5


def test_a_task_left_waiting_is_not_repeated(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    fake_llm.queue(harness.Reply(text='forgot to route it'))
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        await fake.wait('triage/task-1')
        await fake.until(
            lambda: any('still waiting' in n for n in fake.notices)
        )

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    assert len(fake_llm.requests) == 1
    # The repeat claimed it again before seeing it was the same unit.
    assert testing_tasks.show('bakery:1')['claim'] is None


def test_triage_blocks_tasks_where_they_are(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    testing_tasks.task(
        'add', 'needs the operator', '-n', 'bakery', '--tag', 'human',
        '--question', 'which?',
    )  # fmt: skip
    blocking = ('bakery:1', 'set', '--json', '--blocked-by')
    fake_llm.queue(
        # bakery:3 is in build, asking nothing: already decided.
        task(*blocking, 'bakery:3:decided'),
        task(
            *blocking, 'bakery:4:decided', '--blocked-by', '1',
            '--blocked-by', 'https://example.com/1: released',
        ),
        harness.Reply(text='1 blocked'),
    )  # fmt: skip
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', 'queue', None)
        await fake.wait('triage/task-1')

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    refused, _ = fake_llm.tool_results(2)
    assert 'bakery:3 is already decided' in refused
    blocked = testing_tasks.show('bakery:1')
    assert (blocked['tag'], blocked['claim']) == ('triage', None)
    assert testing_tasks.blocked_by('bakery:1') == [
        'bakery:4',
        '1',
        'https://example.com/1',
    ]
    # Hidden from the queue until all it waits on has cleared.
    assert [t['id'] for t in triage.waiting()] == []


def test_recheck_hands_over_what_waits_outside(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    for ident, blockers in (
        (4, ('https://example.com/a: released',)),
        (5, ('https://example.com/a: released', 'default:1')),
        (
            6,
            (
                'https://example.com/a: released',
                'https://example.com/b: merged',
            ),
        ),
        (7, ('bakery:3',)),
        (8, ('https://example.com/b: merged',)),
    ):
        testing_tasks.task(
            'add', f't{ident}', '-n', 'bakery',
            *(arg for b in blockers for arg in ('--blocked-by', b)),
        )  # fmt: skip
    ops.Writer(actors.Actor.agent('someone/else')).claim(
        refs.Ref.parse('bakery:8')
    )
    fake_llm.queue(
        task('bakery:4', 'unset', '--blocked-by', 'https://example.com/a'),
        task('bakery:5', 'unset', '--blocked-by', 'https://example.com/a'),
        harness.Reply(text='a released'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        rechecked = await fake.wait(gateway.trigger('triage', 'recheck', None))
        assert rechecked.final_text == 'a released'

    testing_gateway.run_gateway(triage_claws(root, RECHECK), fake, scenario)
    assert prompt(fake_llm, 0).endswith(
        f'<blocked tasks="3" urls="2">\n{triage.UNTRUSTED}\n'
        '## https://example.com/a\n'
        '- #bakery:4 "t4": released\n'
        '- #bakery:5 "t5": released\n'
        '- #bakery:6 "t6": released\n'
        '## https://example.com/b\n'
        '- #bakery:6 "t6": merged\n'
        '</blocked>'
    )
    # Back in the queue, with task's own note of what cleared.
    assert [t['id'] for t in triage.waiting()] == [1, 4]
    assert testing_tasks.said('bakery:4') == [
        'Cleared: https://example.com/a: released'
    ]
    # Still waiting on the operator's own task.
    assert testing_tasks.blocked_by('bakery:5') == ['1']
    # Rechecked, so claimed, then released when its run ended.
    assert testing_tasks.show('bakery:5')['claim'] is None


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
        for i in range(4):  # a burst of changes...
            (watched / 'sub' / f'{i}.md').write_text(str(i))
            await asyncio.sleep(0.1)
        assert not fake.finished
        await fake.until(lambda: fake.finished)
        # ...is one run: a claw's runs are serial and outlast a poll, so any
        # other fire (at startup, or again after this one) is still queued.
        status = await testing_gateway.claw_status('a')
        assert (status['active'], status['queued']) == ([], [])

    testing_gateway.run_gateway(claws, fake, scenario)
    assert [r.trigger for r in fake.finished] == ['watch:react']
    assert len(fake_llm.requests) == 1
