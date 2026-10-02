import asyncio
import pathlib
import shutil
import tempfile
import time
from collections.abc import Iterator
from collections.abc import Mapping

import pytest

from bakery.gateway import channel
from bakery.gateway import control
from bakery.gateway import core
from bakery.gateway import ledger
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply


@pytest.fixture(name='root', scope='function')
def fixture_root(
    fake_llm: harness.FakeLLM, monkeypatch: pytest.MonkeyPatch
) -> Iterator[pathlib.Path]:
    # Short, because the state dir holds unix sockets (104-byte path cap).
    root = pathlib.Path(tempfile.mkdtemp(prefix='bk', dir='/tmp'))
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    monkeypatch.setenv('BAKERY_FAKE_LLM_URL', fake_llm.url)
    yield root
    shutil.rmtree(root)


async def ctl(record: Mapping[str, object]) -> object:
    """A control request, as the CLI sends it."""
    return await asyncio.to_thread(control.send, record)


def test_run_settles_then_resumes_same_session(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': '[[job]]\nname = "daily"\ncron = "0 7 * * *"\n'
               'prompt = "sweep"\n'}
    )  # fmt: skip
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        Reply(tool='bash', args={'command': 'echo hi'}, prompt_tokens=2),
        Reply(text='swept', prompt_tokens=1),
    )

    async def scenario(gateway: core.Gateway) -> None:
        data = await ctl({'type': 'trigger', 'claw': 'scout'})
        assert isinstance(data, dict)
        key = data['work_key']
        first = await fake.wait(key)
        assert (first.status, first.final_text) == ('settled', 'swept')
        assert (first.turns, first.cost_usd) == (2, 3.0)

        await ctl({'type': 'resume', 'target': key})
        second = await fake.wait(key)
        assert second.status == 'settled'
        assert 'swept' in fake_llm.transcript()

        budget = await ctl({'type': 'budget'})
        assert isinstance(budget, dict)
        assert budget['claws']['scout']['spent_usd'] == 3.0
        status = await ctl({'type': 'status'})
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
            [Reply(tool='bash', args={'command': 'true'}, prompt_tokens=3)],
            'cost limit reached ($2.00)',
        ),
        (
            'turns = 2',
            [Reply(tool='bash', args={'command': 'true'})] * 5,
            'turn limit reached (2)',
        ),
        ('minutes = 0.01', [Reply(delay=5)], 'time limit reached (0.01m)'),
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
    fake_llm.queue(Reply(prompt_tokens=3))

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


def test_dialogs_go_to_the_channel_off_the_clock(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root,
        {'build': '[limits]\nminutes = 0.01\n'},
        extensions={'build': ['confirm_bash.ts']},
    )

    async def slow_yes(
        run: channel.RunInfo, request: dict[str, object]
    ) -> dict[str, object]:
        del run, request
        await asyncio.sleep(1)  # longer than the 0.6s run limit
        return {'confirmed': True}

    fake = testing_gateway.FakeChannel(dialogs=slow_yes)
    fake_llm.queue(Reply(tool='bash', args={'command': 'echo approved'}))

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('build', None, 'go'))
        assert run.status == 'settled', run.reason

    testing_gateway.run_gateway(claws, fake, scenario)
    assert [r['method'] for r in fake.asked] == ['confirm']
    assert 'approved' in fake_llm.transcript()


def test_extension_errors_fail_the_run(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': ''}, extensions={'scout': ['faulty.ts']}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(Reply(tool='bash', args={'command': 'true'}))

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('scout', None, 'go'))
        assert run.status == 'failed'
        assert 'faulty extension' in (run.reason or '')

    testing_gateway.run_gateway(claws, fake, scenario)


def test_slots_pause_and_shutdown(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '', 'b': ''}, gateway={'max_processes': 1}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(Reply(delay=1), Reply(), Reply(delay=30))

    async def scenario(gateway: core.Gateway) -> None:
        first = gateway.trigger('a', None, 'first')
        second = gateway.trigger('b', None, 'second')
        await asyncio.sleep(0.3)
        assert list(gateway.active) == [first]  # one process for everyone
        assert [r.work_key for r in gateway.queue] == [second]
        assert (await fake.wait(first)).status == 'settled'
        assert (await fake.wait(second)).status == 'settled'

        await ctl({'type': 'pause', 'target': 'a'})
        held = gateway.trigger('a', None, 'held')
        await asyncio.sleep(0.3)
        assert not gateway.active
        await ctl({'type': 'resume', 'target': 'a'})
        while not gateway.active:
            await asyncio.sleep(0.05)
        # The third reply hangs; stopping the gateway interrupts it.
        assert held in gateway.active

    started = time.monotonic()
    testing_gateway.run_gateway(claws, fake, scenario)
    assert time.monotonic() - started < 20
    assert fake.finished[-1].status == 'interrupted'


def test_pause_abort_parks_in_flight_runs(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'a': ''})
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(Reply(delay=30))

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('a', None, 'slow')
        while key not in fake.started:
            await asyncio.sleep(0.05)
        await ctl({'type': 'pause', 'target': 'all', 'abort': True})
        run = await fake.wait(key)
        assert (run.status, run.reason) == ('parked', 'paused')

    testing_gateway.run_gateway(claws, fake, scenario)


def test_silent_jobs_report_no_text(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '[[job]]\nname = "beat"\ncron = "*/30 * * * *"\n'
               'prompt = "check"\nsilent_ok = true\n'}
    )  # fmt: skip
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(Reply(text='NO_REPLY'), Reply(text='something happened'))

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
                await ctl(record)

        (claws / 'a' / 'claw.toml').write_text('[limits]\nturns = 0\n')
        with pytest.raises(control.ControlError, match='turns: must be'):
            await ctl({'type': 'reload'})
        assert gateway.config.claw('a').limits.turns == 50

    testing_gateway.run_gateway(claws, fake, scenario)
