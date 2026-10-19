import asyncio
import pathlib
import sqlite3
import threading

import pytest

from bakery.gateway import channel
from bakery.gateway import control
from bakery.gateway import core
from bakery.gateway import ledger
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks


def active_keys(claw_status: dict[str, object]) -> list[object]:
    active = claw_status['active']
    assert isinstance(active, list)
    return [run['work_key'] for run in active]


def test_run_settles_then_resumes_same_session(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': '[[job]]\nname = "daily"\ncron = "0 7 * * *"\n'
               'prompt = "sweep"\n'}
    )  # fmt: skip
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.Reply(
            tool='bash', args={'command': 'echo hi'}, prompt_tokens=2
        ),
        harness.Reply(text='swept', prompt_tokens=1),
    )

    async def scenario(gateway: core.Gateway) -> None:
        data = await testing_gateway.ctl({'type': 'trigger', 'claw': 'scout'})
        assert isinstance(data, dict)
        key = data['work_key']
        first = await fake.wait(key)
        assert (first.status, first.final_text) == ('settled', 'swept')
        assert (first.turns, first.cost_usd) == (2, 3.0)

        await testing_gateway.ctl({'type': 'resume', 'target': key})
        second = await fake.wait(key)
        assert second.status == 'settled'
        assert 'swept' in fake_llm.transcript()

        budget = await testing_gateway.ctl({'type': 'budget'})
        assert isinstance(budget, dict)
        assert budget['claws']['scout']['spent_usd'] == 3.0
        status = await testing_gateway.ctl({'type': 'status'})
        assert isinstance(status, dict)
        assert 'daily' in status['claws']['scout']['next']
        assert gateway.runs.last_claw(key) == 'scout'

    testing_gateway.run_gateway(claws, fake, scenario)
    assert [r.trigger for r in fake.finished] == ['manual:daily', 'resume']


@pytest.mark.parametrize(
    ('limits', 'replies', 'reason'),
    [
        (
            'cost_usd = 2.0',
            [
                harness.Reply(
                    tool='bash', args={'command': 'true'}, prompt_tokens=3
                )
            ],
            'cost limit reached ($2.00)',
        ),
        (
            'turns = 2',
            [harness.Reply(tool='bash', args={'command': 'true'})] * 5,
            'turn limit reached (2)',
        ),
        (
            'minutes = 0.01',
            [harness.Reply(hold=threading.Event())],
            'time limit reached (0.01m)',
        ),
    ],
)
def test_limits_park_runs(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    limits: str,
    replies: list[harness.Reply],
    reason: str,
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': f'[limits]\n{limits}\n'}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(*replies)

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('scout', None, 'go')
        run = await fake.wait(key)
        assert (run.status, run.reason) == (ledger.Status.parked, reason)

    testing_gateway.run_gateway(claws, fake, scenario)


def test_daily_budget_parks_without_calling_the_model(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': '[limits]\ndaily_cost_usd = 2.0\n'}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(harness.Reply(prompt_tokens=3))

    async def scenario(gateway: core.Gateway) -> None:
        first = await fake.wait(gateway.trigger('scout', None, 'one'))
        assert first.status == 'parked'  # $3 run hits the $2 day budget
        second = await fake.wait(gateway.trigger('scout', None, 'two'))
        assert (second.status, second.reason) == (
            'parked',
            'daily budget exhausted',
        )

    testing_gateway.run_gateway(claws, fake, scenario)
    assert len(fake_llm.requests) == 1


def test_dialogs_are_cancelled_with_a_notice(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'build': ''}, extensions={'build': ['confirm_bash.ts']}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.Reply(tool='bash', args={'command': 'echo approved'}),
        harness.Reply(text='carried on'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('build', None, 'go')
        run = await fake.wait(key)
        assert (run.status, run.final_text) == ('settled', 'carried on')
        print(fake.notices)
        assert (
            f"{key}: cancelled a confirm dialog ('Run bash?');"
            ' claws ask on the task list'
        ) in fake.notices

    testing_gateway.run_gateway(claws, fake, scenario)
    assert 'denied by the operator' in fake_llm.transcript()


def test_extension_errors_fail_the_run(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': ''}, extensions={'scout': ['faulty.ts']}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(harness.Reply(tool='bash', args={'command': 'true'}))

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('scout', None, 'go'))
        assert run.status == 'failed'
        assert 'faulty extension' in (run.reason or '')

    testing_gateway.run_gateway(claws, fake, scenario)


class DeadThread(testing_gateway.FakeChannel):
    """A channel whose run reports fail, like a deleted Discord thread."""

    async def run_started(self, info: channel.RunInfo) -> None:
        raise RuntimeError('Unknown Channel')

    async def run_progress(self, info: channel.RunInfo) -> None:
        raise RuntimeError('Unknown Channel')


def test_reporting_errors_do_not_fail_the_run(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'scout': ''})
    fake = DeadThread()
    fake_llm.queue(
        harness.Reply(tool='bash', args={'command': 'true'}),
        harness.Reply(text='done'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('scout', None, 'go')
        run = await fake.wait(key)
        assert (run.status, run.final_text) == ('settled', 'done')
        print(fake.notices)
        reported = [n for n in fake.notices if key in n]
        assert len(reported) == 1
        assert 'Unknown Channel' in reported[0]

    testing_gateway.run_gateway(claws, fake, scenario)


def test_slots_pause_and_shutdown(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '', 'b': ''}, gateway={'max_processes': 1}
    )
    fake = testing_gateway.FakeChannel()
    release = threading.Event()
    fake_llm.queue(
        harness.Reply(hold=release),
        harness.Reply(),
        harness.Reply(),
        harness.Reply(hold=threading.Event()),
    )

    async def scenario(gateway: core.Gateway) -> None:
        first = gateway.trigger('a', None, 'first')
        second = gateway.trigger('b', None, 'second')
        await asyncio.to_thread(fake_llm.wait_requests, 1)
        a = await testing_gateway.claw_status('a')
        b = await testing_gateway.claw_status('b')
        # One process for everyone: b's run waits for a's.
        assert active_keys(a) == [first]
        assert (b['active'], b['queued']) == ([], [second])
        release.set()
        assert (await fake.wait(first)).status == 'settled'
        assert (await fake.wait(second)).status == 'settled'

        await testing_gateway.ctl({'type': 'pause', 'target': 'a'})
        held = gateway.trigger('a', None, 'held')
        # Queued after the held run, so it only goes first if a is paused.
        after = gateway.trigger('b', None, 'after')
        assert (await fake.wait(after)).status == 'settled'
        a = await testing_gateway.claw_status('a')
        assert (a['active'], a['queued']) == ([], [held])
        await testing_gateway.ctl({'type': 'resume', 'target': 'a'})
        await asyncio.to_thread(fake_llm.wait_requests, 4)
        # Its reply hangs; stopping the gateway interrupts it.
        assert active_keys(await testing_gateway.claw_status('a')) == [held]

    testing_gateway.run_gateway(claws, fake, scenario)
    assert fake.finished[-1].status == 'interrupted'


def test_pause_abort_parks_in_flight_runs(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'a': ''})
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(harness.Reply(hold=threading.Event()))

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('a', None, 'slow')
        await asyncio.to_thread(fake_llm.wait_requests, 1)  # in flight
        await testing_gateway.ctl(
            {'type': 'pause', 'target': 'all', 'abort': True}
        )
        run = await fake.wait(key)
        assert (run.status, run.reason) == ('parked', 'paused')

    testing_gateway.run_gateway(claws, fake, scenario)


def test_claims_do_not_outlive_runs(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    # Left behind by a run the gateway never saw finish (eg. a crash).
    testing_tasks.task(
        'add', 'stranded', '-n', 'bakery', '--tag', 'triage',
        '--owner', 'triage',
    )  # fmt: skip
    testing_tasks.task('add', 'next', '-n', 'bakery', '--tag', 'triage')
    testing_tasks.task('add', 'theirs', '-n', 'bakery', '--owner', 'kevin')
    claws = testing_gateway.make_claws(
        root,
        {'triage': ''},
        extensions={'triage': ['pi/claw-extensions/task.ts']},
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.call('task_set', id=2, claim=True),
        harness.Reply(hold=threading.Event()),
    )

    async def scenario(gateway: core.Gateway) -> None:
        await fake.until(lambda: 'gateway started' in fake.notices)
        assert testing_tasks.show('bakery:1')['owner'] is None

        key = gateway.trigger('triage', None, 'go')
        await asyncio.to_thread(fake_llm.wait_requests, 2)  # claimed
        assert testing_tasks.show('bakery:2')['owner'] == 'triage'
        gateway.pause('triage', abort=True)
        assert (await fake.wait(key)).status == 'parked'
        released = f'released bakery:2: triage still claimed it after {key}'
        await fake.until(
            lambda: any(n.startswith(released) for n in fake.notices)
        )
        assert testing_tasks.show('bakery:2')['owner'] is None

    testing_gateway.run_gateway(claws, fake, scenario)
    print(fake.notices)
    assert (
        'released bakery:1: triage still claimed it when the gateway started'
        in fake.notices
    )
    assert testing_tasks.show('bakery:3')['owner'] == 'kevin'
    assert not [n for n in fake.notices if 'cannot release' in n]


def test_silent_jobs_report_no_text(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '[[job]]\nname = "beat"\ncron = "*/30 * * * *"\n'
               'prompt = "check"\nsilent_ok = true\n'}
    )  # fmt: skip
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.Reply(text='NO_REPLY'),
        harness.Reply(text='something happened'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        quiet = await fake.wait(gateway.trigger('a', 'beat', None))
        loud = await fake.wait(gateway.trigger('a', 'beat', None))
        assert quiet.final_text is None
        assert loud.final_text == 'something happened'

    testing_gateway.run_gateway(claws, fake, scenario)


def test_control_rejects_bad_requests_and_configs(root: pathlib.Path) -> None:
    claws = testing_gateway.make_claws(root, {'a': ''})
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        for record, error in (
            ({'type': 'trigger', 'claw': 'nope'}, 'unknown claw: nope'),
            ({'type': 'trigger', 'claw': 'a'}, 'name a job or a --prompt'),
            ({'type': 'resume', 'target': 'a/none'}, 'no runs for a/none'),
            ({'type': 'bogus'}, 'unknown request: bogus'),
        ):
            with pytest.raises(control.ControlError, match=error):
                await testing_gateway.ctl(record)

        loaded = gateway.config
        claw_toml = claws / 'a' / 'claw.toml'
        claw_toml.write_text(claw_toml.read_text() + '[limits]\nturns = 0\n')
        with pytest.raises(control.ControlError, match='turns: must be'):
            await testing_gateway.ctl({'type': 'reload'})
        assert gateway.config == loaded

    testing_gateway.run_gateway(claws, fake, scenario)


def test_secrets_reach_pi_but_not_bash(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    keychain: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secrets = (
        'secrets = ["BAKERY_FAKE_LLM_URL", "GRANTED=BAKERY_TEST_ITEM",'
        ' "SHARED=BAKERY_TEST_SHARED"]\nbash_secrets = ["SHARED"]'
    )
    claws = testing_gateway.make_claws(
        root,
        {
            'a': secrets,
            'b': 'secrets = ["BAKERY_FAKE_LLM_URL", "BAKERY_TEST_MISSING"]\n',
        },
        extensions={'a': ['env_of.ts']},
    )
    keychain['BAKERY_TEST_ITEM'] = 'granted-value'
    keychain['BAKERY_TEST_SHARED'] = 'shared-value'
    monkeypatch.setenv('BAKERY_TEST_PRIVATE', 'private-value')
    # Set in the gateway's environment, but secrets only come from the
    # Keychain.
    monkeypatch.setenv('BAKERY_TEST_MISSING', 'env-value')
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.Reply(tool='env_of', args={'name': 'GRANTED'}),
        harness.Reply(
            tool='bash', args={'command': 'env; git config user.name'}
        ),
    )

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('a', None, 'go'))
        assert run.status == 'settled', run.reason
        pi_env = fake_llm.transcript(-2)
        bash_env = fake_llm.transcript().removeprefix(pi_env)
        assert 'GRANTED=granted-value' in pi_env
        assert 'BAKERY_CLAW=a' in bash_env
        assert 'SHARED=shared-value' in bash_env
        # The claw's home has its git config, with the claws' identity.
        assert 'thekevjames-bakery' in bash_env
        for hidden in ('granted-value', 'private-value', fake_llm.url):
            assert hidden not in bash_env

        missing = await fake.wait(gateway.trigger('b', None, 'go'))
        assert missing.status == 'failed'
        assert 'secret BAKERY_TEST_MISSING is not set' in (
            missing.reason or ''
        )

    testing_gateway.run_gateway(claws, fake, scenario)


def test_ledger_gains_the_job_column(root: pathlib.Path) -> None:
    path = root / 'runs.db'
    with sqlite3.connect(path) as db:
        db.execute(
            'CREATE TABLE runs (id INTEGER PRIMARY KEY, claw TEXT NOT NULL,'
            ' work_key TEXT NOT NULL, trigger TEXT NOT NULL,'
            ' started_at REAL NOT NULL, ended_at REAL, status TEXT NOT NULL,'
            ' reason TEXT, cost_usd REAL NOT NULL DEFAULT 0,'
            ' turns INTEGER NOT NULL DEFAULT 0)'
        )
        db.execute(
            'INSERT INTO runs (claw, work_key, trigger, started_at, status)'
            " VALUES ('a', 'a/old', 'manual', 0, 'settled')"
        )
    runs = ledger.Ledger(path)
    runs.start('a', 'a/new', 'cron:dream', 'dream')
    assert (runs.last_job('a/old'), runs.last_job('a/new')) == (None, 'dream')
    runs.close()
