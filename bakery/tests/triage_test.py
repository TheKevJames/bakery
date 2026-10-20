import asyncio
import json
import pathlib
import shlex

import pytest
from task import actors
from task import ops
from task import schema

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
BLOCKED = """
[[job]]
name = "queue"
cron = "0 * * * *"
prompt = "Triage this task."
collector = "triage"
[[job]]
name = "unblock"
cron = "0 * * * *"
prompt = "Unblock these tasks."
collector = "triage-unblock"
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


def test_blocking_a_task_needs_blockers_which_have_not_cleared(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    testing_tasks.task(
        'add', 'needs the operator', '-n', 'bakery', '--tag', 'human'
    )
    valid = (
        '**Blocked on:**\n- #bakery:4 decided\n- #default:1 done\n'
        '- https://example.com/1: released'
    )
    blocking = ('bakery:1', 'set', '--tag', 'blocked', '--comment')
    fake_llm.queue(
        task(*blocking, '**Blocked on:**\n- see upstream'),
        task(
            *blocking,
            '**Blocked on:**\n- #bakery:3 decided'
            '\n- #bakery:99 done\n- #bakery:1 done\n- #default:1 decided',
        ),
        task(*blocking, valid),
        harness.Reply(text='1 blocked'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', 'queue', None)
        await fake.wait('triage/task-1')

    testing_gateway.run_gateway(triage_claws(root, BLOCKED), fake, scenario)
    malformed, cleared = fake_llm.tool_results(2)
    assert "'see upstream' is not `#<namespace>:<id> decided`" in malformed
    assert '#bakery:3 decided: already cleared (build)' in cleared
    assert (
        '#bakery:99 done: already cleared'
        ' (#bakery:99 is done or does not exist)' in cleared
    )
    assert '#bakery:1 done: a task cannot block itself' in cleared
    assert (
        "#default:1 decided: #default:1 is the operator's (dev),"
        ' so use `#default:1 done`' in cleared
    )
    blocked = testing_tasks.show('bakery:1')
    assert (blocked['tag'], blocked['claim']) == ('blocked', None)
    # Refused notes were not kept.
    assert testing_tasks.said('bakery:1') == [valid]


def test_blocked_tasks_go_back_once_their_blockers_clear(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    # bakery:1 (triage) is undecided, default:1 (the operator's) is not done,
    # bakery:3 (build) is decided, and bakery:4 is done.
    testing_tasks.task('add', 't4', '-n', 'bakery', '--tag', 'build')
    testing_tasks.task('bakery:4', 'done')
    for ident, blockers in (
        (5, '- #bakery:3 decided'),
        (6, '- #bakery:1 decided'),
        (7, '- #bakery:4 done\n- https://example.com/a: released'),
        (8, '- #default:1 done\n- https://example.com/a: released'),
        (
            9,
            '- https://example.com/a: released\n'
            '- https://example.com/b: merged',
        ),
        (10, ''),
        (11, '- #bakery:3 decided'),
    ):
        notes = (
            f'## Triage\n\n**Blocked on:**\n{blockers}' if blockers else 'x'
        )
        testing_tasks.task(
            'add',
            f't{ident}',
            '-n',
            'bakery',
            '--tag',
            'blocked',
            '--description',
            notes,
        )
    ops.Writer(actors.Actor.agent('someone/else')).claim(
        schema.Ref.parse('bakery:11')
    )
    fake_llm.queue(
        task(
            'bakery:5', 'set', '--tag', 'triage', '--comment', '## Unblocked'
        ),
        harness.Reply(text='moved 5'),
        harness.Reply(text='nothing cleared'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        unblocked = await fake.wait(gateway.trigger('triage', 'unblock', None))
        assert unblocked.final_text == 'moved 5'
        rechecked = await fake.wait(gateway.trigger('triage', 'recheck', None))
        assert rechecked.final_text == 'nothing cleared'

    testing_gateway.run_gateway(triage_claws(root, BLOCKED), fake, scenario)
    assert prompt(fake_llm, 0).endswith(
        f'<unblocked tasks="1">\n{triage.UNTRUSTED}\n'
        '- #bakery:5 "t5": cleared #bakery:3 decided\n</unblocked>'
    )
    assert prompt(fake_llm, 2).endswith(
        f'<blocked tasks="2" urls="2">\n{triage.UNTRUSTED}\n'
        '## https://example.com/a\n'
        '- #bakery:7 "t7": released\n'
        '- #bakery:9 "t9": released\n'
        '## https://example.com/b\n'
        '- #bakery:9 "t9": merged\n'
        '</blocked>'
    )
    assert (
        testing_tasks.show('bakery:5')['tag'],
        testing_tasks.show('bakery:5')['claim'],
    ) == ('triage', None)
    # Rechecked, so claimed, then released when its run ended.
    assert testing_tasks.show('bakery:7')['claim'] is None
    # Reported weekly by recheck, not hourly by unblock.
    assert [n for n in fake.notices if '#bakery:10' in n] == [
        'triage/recheck collection problems:\n'
        'blocked #bakery:10: no **Blocked on:** list'
    ]


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
