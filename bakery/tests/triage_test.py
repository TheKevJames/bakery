import asyncio
import json
import pathlib
import subprocess

import pytest

from bakery.collectors import triage
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
BLOCKED = """
[policy]
tools = ["task_list", "task_show", "task_set"]
[[job]]
name = "queue"
cron = "0 * * * *"
prompt = "Triage this ticket."
collector = "triage"
[[job]]
name = "unblock"
cron = "0 * * * *"
prompt = "Unblock these tickets."
collector = "triage-unblock"
[[job]]
name = "recheck"
cron = "0 6 * * 1"
prompt = "Recheck these blocked tickets."
collector = "triage-recheck"
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
    task('add', 'recurring', '--tag', 'bakery/triage', '--next', '2030-01-01')
    task('add', 'from kevin')
    task('add', 'already routed', '--tag', 'bakery/build')
    return folder


def prompt(fake_llm: harness.FakeLLM, index: int) -> str:
    messages = fake_llm.requests[index]['messages']
    assert isinstance(messages, list)
    content = next(m for m in messages if m['role'] == 'user')['content']
    assert isinstance(content, list)
    return ''.join(str(part['text']) for part in content)


def triage_claws(root: pathlib.Path, claw_toml: str = QUEUE) -> pathlib.Path:
    return testing_gateway.make_claws(
        root,
        {'triage': claw_toml},
        extensions={'triage': ['pi/claw-extensions/task.ts']},
    )


def test_works_through_the_queue_one_ticket_per_unit(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    task('add', 'handed over by kevin', '--tag', 'bakery/triage')
    fake_llm.queue(
        call(id=1, claim=True),
        call(id=1, priority='high', size='small', description_append='notes'),
        call(id=1, tag='bakery/build', release=True),
        Reply(text='1 to build'),
        call(id=5, claim=True),
        call(id=5, tag='bakery/wontfix', priority='low', size='small'),
        call(id=5, release=True),
        Reply(text='5 to wontfix'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        first = await fake.wait('triage/task-1')
        assert (first.status, first.final_text) == ('settled', '1 to build')
        second = await fake.wait('triage/task-5')
        assert (second.status, second.final_text) == (
            'settled',
            '5 to wontfix',
        )
        # The repeat after it finds nothing left: #2 is scheduled, and #3
        # is in Kevin's own inbox.
        await asyncio.sleep(0.5)
        assert not gateway.active and not gateway.queue

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    assert json.dumps('<ticket id="1" waiting="1">')[1:-1] in (
        fake_llm.transcript(0)
    )
    first, second = show(1), show(5)
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
    assert show(2)['tag'] == 'bakery/triage'  # scheduled: not triaged
    assert (show(3)['tag'], show(3)['owner']) == ('triage', None)
    assert len(fake_llm.requests) == 8


def test_a_ticket_left_waiting_is_not_repeated(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    fake_llm.queue(Reply(text='forgot to route it'))
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        await fake.wait('triage/task-1')
        async with asyncio.timeout(10):
            while not any('still waiting' in n for n in fake.notices):
                await asyncio.sleep(0.05)

    testing_gateway.run_gateway(triage_claws(root), fake, scenario)
    assert len(fake_llm.requests) == 1


def test_blocking_a_ticket_needs_blockers_which_have_not_cleared(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    task('add', 'needs kevin', '--tag', 'bakery/human')
    valid = (
        '**Blocked on:**\n- #5 decided\n- #3 done\n'
        '- https://example.com/1: released'
    )
    fake_llm.queue(
        call(id=1, claim=True),
        call(
            id=1,
            tag='bakery/blocked',
            description_append='**Blocked on:**\n- see upstream',
        ),
        call(
            id=1,
            tag='bakery/blocked',
            description_append='**Blocked on:**\n- #4 decided\n- #99 done'
            '\n- #1 done\n- #3 decided',
        ),
        call(id=1, tag='bakery/blocked', description_append=valid),
        call(id=1, release=True),
        Reply(text='1 blocked'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', 'queue', None)
        await fake.wait('triage/task-1')

    testing_gateway.run_gateway(triage_claws(root, BLOCKED), fake, scenario)
    malformed, cleared = fake_llm.tool_results(3)[-2:]
    assert "'see upstream' is not `#<id> decided`" in malformed
    assert '#4 decided: already cleared (bakery/build)' in cleared
    assert '#99 does not exist' in cleared
    assert '#1 done: a task cannot block itself' in cleared
    assert "#3 decided: #3 is Kevin's (triage), so use `#3 done`" in cleared
    ticket = show(1)
    assert (ticket['tag'], ticket['owner']) == ('bakery/blocked', None)
    assert ticket['description'] == valid  # refused notes were not appended


def test_blocked_tickets_go_back_once_their_blockers_clear(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    # #1 (bakery/triage) is undecided, #3 (Kevin's) is not done,
    # #4 (bakery/build) is decided, and #5 is done.
    task('add', 't5', '--tag', 'bakery/build')
    task('5', 'done')
    for ident, blockers in (
        (6, '- #4 decided'),
        (7, '- #1 decided'),
        (8, '- #5 done\n- https://example.com/a: released'),
        (9, '- #3 done\n- https://example.com/a: released'),
        (
            10,
            '- https://example.com/a: released\n'
            '- https://example.com/b: merged',
        ),
        (11, ''),
        (12, '- #4 decided'),
    ):
        notes = (
            f'## Triage\n\n**Blocked on:**\n{blockers}' if blockers else 'x'
        )
        task(
            'add',
            f't{ident}',
            '--tag',
            'bakery/blocked',
            '--description',
            notes,
        )
    task('set', '12', '--owner', 'someone')
    fake_llm.queue(
        call(id=6, claim=True),
        call(
            id=6,
            tag='bakery/triage',
            description_append='## Unblocked',
            release=True,
        ),
        Reply(text='moved 6'),
        Reply(text='nothing cleared'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        unblocked = await fake.wait(gateway.trigger('triage', 'unblock', None))
        assert unblocked.final_text == 'moved 6'
        rechecked = await fake.wait(gateway.trigger('triage', 'recheck', None))
        assert rechecked.final_text == 'nothing cleared'

    testing_gateway.run_gateway(triage_claws(root, BLOCKED), fake, scenario)
    assert prompt(fake_llm, 0).endswith(
        f'<unblocked tickets="1">\n{triage.UNTRUSTED}\n'
        '- #6 "t6": cleared #4 decided\n</unblocked>'
    )
    assert prompt(fake_llm, 3).endswith(
        f'<blocked tickets="2" urls="2">\n{triage.UNTRUSTED}\n'
        '## https://example.com/a\n'
        '- #8 "t8": released\n'
        '- #10 "t10": released\n'
        '## https://example.com/b\n'
        '- #10 "t10": merged\n'
        '</blocked>'
    )
    assert (show(6)['tag'], show(6)['owner']) == ('bakery/triage', None)
    # Reported weekly by recheck, not hourly by unblock.
    assert [n for n in fake.notices if '#11' in n] == [
        'triage/recheck collection problems:\n'
        'bakery/blocked #11: no **Blocked on:** list'
    ]


def test_claws_only_change_bakery_tasks(
    root: pathlib.Path, tasks: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    del tasks
    # #4 is claimed, then Kevin takes it over into his own section.
    task('set', '4', '--owner', 'triage', '--tag', 'dev')
    fake_llm.queue(
        call(id=3, claim=True),
        call(id=3, description_append='notes'),
        Reply(tool='task_link', args={'id': 3, 'url': 'https://x'}),
        Reply(tool='task_done', args={'id': 3}),
        call(id=1, tag='dev'),
        call(id=4, release=True),
        Reply(text='done'),
    )
    fake = testing_gateway.FakeChannel()
    claw_toml = QUEUE.replace(
        '"task_set"', '"task_set", "task_link", "task_done"'
    )

    async def scenario(gateway: core.Gateway) -> None:
        gateway.trigger('triage', None, None)
        await fake.wait('triage/task-1')

    testing_gateway.run_gateway(triage_claws(root, claw_toml), fake, scenario)
    *refused, moved, released = fake_llm.tool_results()
    assert len(refused) == 4
    assert all("task 3 is Kevin's (tagged triage)" in r for r in refused)
    assert 'dev is not under bakery/' in moved
    assert '"owner": null' in released
    kevins = show(3)
    assert (kevins['owner'], kevins['description'], kevins['link']) == (
        None,
        None,
        None,
    )
    assert (kevins['tag'], kevins['done']) == ('triage', None)
    assert (show(1)['tag'], show(4)['owner']) == ('bakery/triage', None)


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
