import asyncio
import pathlib
from collections.abc import Awaitable
from collections.abc import Callable

import pytest

from bakery.chat import relay
from bakery.chat import transport
from bakery.gateway import core
from testing import chat
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply
ASK = 'pi/claw-extensions/ask-user.ts'


def run(
    claws: pathlib.Path,
    scenario: Callable[
        [core.Gateway, relay.Relay, chat.FakeTransport], Awaitable[None]
    ],
    latency: float = 0.0,
) -> None:
    fake = chat.FakeTransport(latency)
    bot = relay.Relay(fake, chat.OWNER)

    async def wrapped(gateway: core.Gateway) -> None:
        await scenario(gateway, bot, fake)

    testing_gateway.run_gateway(claws, bot, wrapped)


async def until(predicate: Callable[[], object]) -> None:
    async with asyncio.timeout(chat.WAIT_TIMEOUT):
        while not predicate():
            await asyncio.sleep(0.02)


def test_gateway_runs_get_a_thread_with_status_and_reply(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'scout': ''})
    long_reply = '\n'.join(f'line {i} ' + 'x' * 300 for i in range(40))
    fake_llm.queue(
        Reply(tool='bash', args={'command': 'true'}), Reply(text='all done')
    )
    fake_llm.queue(Reply(text=long_reply))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        del bot
        await fake.until(lambda: 'gateway started' in fake.notices())
        key = gateway.trigger('scout', None, 'sweep')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        await fake.message(thread, 'all done')
        status = await fake.message(thread, '✅')
        assert f'{key} settled' in status.text
        assert fake.threads[thread].claw == 'scout'

        gateway.message('scout', key, 'more please')
        await fake.until(
            lambda: any(m.attachment for m in fake.in_thread(thread))
        )
        [attached] = [m for m in fake.in_thread(thread) if m.attachment]
        assert attached.attachment is not None
        assert attached.attachment.data.decode() == long_reply
        # Both runs of the unit share its one thread.
        assert len(fake.threads) == 1

    run(claws, scenario)


def test_owner_messages_start_and_continue_threads(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'triage': ''})
    fake_llm.queue(Reply(text='first answer'), Reply(text='second answer'))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        await bot.on_message(chat.STRANGER, 'triage', None, 500, 'hi')
        assert not fake.threads

        await bot.on_message(chat.OWNER, 'triage', None, 501, 'look at #151')
        [(thread, info)] = fake.threads.items()
        assert (info.name, info.from_message) == ('look at #151', 501)
        await fake.message(thread, 'first answer')
        assert 'look at #151' in fake_llm.transcript()

        await bot.on_message(chat.OWNER, 'triage', thread, 502, 'and #152?')
        await fake.message(thread, 'second answer')
        assert 'and #152?' in fake_llm.transcript()
        assert fake.messages[502].reactions == ['👀']
        assert gateway.runs.work_key_of_thread(thread) == (
            f'triage/thread-{thread}'
        )

        await bot.on_message(chat.OWNER, 'triage', 9999, 503, 'not ours')
        assert 503 not in fake.messages

    run(claws, scenario)


def test_messages_steer_in_flight_runs(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'build': ''})
    fake_llm.queue(
        Reply(tool='bash', args={'command': 'sleep 1'}), Reply(text='done')
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        await bot.on_message(chat.OWNER, 'build', None, 600, 'start')
        [thread] = fake.threads
        await until(lambda: len(fake_llm.requests) == 1)
        await bot.on_message(chat.OWNER, 'build', thread, 601, 'use tabs')
        await fake.message(thread, '✅')
        assert 'use tabs' in fake_llm.transcript()
        # One run: the message joined it rather than queueing another.
        assert len(fake_llm.requests) == 2
        assert not gateway.queue

    run(claws, scenario)


def test_confirm_dialogs_are_buttons(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'build': ''}, extensions={'build': ['confirm_bash.ts']}
    )
    fake_llm.queue(Reply(tool='bash', args={'command': 'echo approved'}))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        key = gateway.trigger('build', None, 'go')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        ask = await fake.message(thread, f'<@{chat.OWNER}> **Run bash?**')
        approve = ask.button('Approve').custom_id

        refused = await bot.on_component(chat.STRANGER, approve, None)
        assert refused == relay.NOT_OWNER
        assert await bot.on_component(chat.OWNER, approve, None) == 'Done.'
        await fake.message(thread, '✅')
        assert 'approved' in fake_llm.transcript()
        assert ask.text.endswith('→ approved')
        assert ask.components == ()
        expired = await bot.on_component(chat.OWNER, approve, None)
        assert expired == relay.EXPIRED

    run(claws, scenario)


def test_ask_user_is_answered_by_a_thread_reply(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'triage': ''}, extensions={'triage': [ASK]}
    )
    fake_llm.queue(
        Reply(
            tool='ask_user',
            args={'question': 'Which repo?', 'assumption': 'all'},
        ),
        Reply(text='thanks'),
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        key = gateway.trigger('triage', None, 'go')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        ask = await fake.message(thread, f'<@{chat.OWNER}> **Which repo?**')
        assert 'if unanswered: all' in ask.text
        await bot.on_message(chat.OWNER, 'triage', thread, 700, 'tools')
        await fake.message(thread, '✅')
        assert 'The user answered: tools' in fake_llm.transcript()
        assert fake.messages[700].reactions == ['✅']

    run(claws, scenario)


# With Discord latency, pi's own dialog timeout fires before the relay's,
# and parking the run cancels the relay's wait for an answer.
@pytest.mark.parametrize('latency', [0.0, 0.2])
def test_unanswered_questions_park_until_resumed(
    root: pathlib.Path, fake_llm: harness.FakeLLM, latency: float
) -> None:
    claws = testing_gateway.make_claws(
        root,
        {'build': '[ask]\ntimeout_hours = 0.0002\n'},  # under a second
        extensions={'build': [ASK]},
    )
    fake_llm.queue(
        Reply(
            tool='ask_user', args={'question': 'Merge?', 'assumption': 'no'}
        ),
        Reply(text='resumed fine'),
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        key = gateway.trigger('build', None, 'go')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        parked = await fake.message(thread, f'<@{chat.OWNER}> {key} parked')
        assert 'waiting for an answer: Merge?' in parked.text
        ask = await fake.message(thread, f'<@{chat.OWNER}> **Merge?**')
        await fake.until(lambda: ask.text.endswith('→ timed out'))
        # Parking ends the run: no model call after the question.
        assert len(fake_llm.requests) == 1

        resume = parked.button('Resume').custom_id
        assert await bot.on_component(chat.OWNER, resume, None) == (
            f'Resuming {key}.'
        )
        await fake.message(thread, 'resumed fine')

    run(claws, scenario, latency)


def test_silent_jobs_only_post_when_they_have_news(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '[[job]]\nname = "beat"\ncron = "0 * * * *"\n'
               'prompt = "check"\nsilent_ok = true\n'}
    )  # fmt: skip
    fake_llm.queue(Reply(text='NO_REPLY'), Reply(text='news!'))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        del bot
        quiet = gateway.trigger('a', 'beat', None)
        await until(
            lambda: gateway.runs.last_claw(quiet) and not gateway.active
        )
        loud = gateway.trigger('a', 'beat', None)
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(loud)
        await fake.message(thread, 'news!')
        assert len(fake.threads) == 1

    run(claws, scenario)


def test_commands(root: pathlib.Path, fake_llm: harness.FakeLLM) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': '[limits]\ndaily_cost_usd = 1.0\n'}
    )
    fake_llm.queue(Reply(prompt_tokens=2))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        refused = await bot.on_command(chat.STRANGER, 'status', {})
        assert refused == transport.Response(relay.NOT_OWNER, private=True)
        error = await bot.on_command(chat.OWNER, 'trigger', {'claw': 'nope'})
        assert error == transport.Response(
            '⚠️ unknown claw: nope', private=True
        )

        await bot.on_command(chat.OWNER, 'pause', {'target': 'scout'})
        status = await bot.on_command(chat.OWNER, 'status', {})
        assert 'scout: paused' in status.text
        await bot.on_command(chat.OWNER, 'resume', {'target': 'scout'})

        first = await bot.on_command(
            chat.OWNER, 'trigger', {'claw': 'scout', 'prompt': 'one'}
        )
        key = first.text.removeprefix('queued ')
        await until(lambda: gateway.runs.last_claw(key) and not gateway.active)
        gateway.trigger('scout', None, 'two')
        await fake.until(
            lambda: 'scout has exhausted its daily budget' in fake.notices()
        )
        budget = await bot.on_command(chat.OWNER, 'budget', {})
        assert '$2.00 / $1.00 today' in budget.text

    run(claws, scenario)


def test_stopping_cancels_pending_questions(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'triage': ''}, extensions={'triage': [ASK]}
    )
    fake_llm.queue(
        Reply(tool='ask_user', args={'question': 'Hm?', 'assumption': 'x'})
    )
    fake = chat.FakeTransport()
    bot = relay.Relay(fake, chat.OWNER)

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('triage', None, 'go')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        await fake.message(thread, f'<@{chat.OWNER}> **Hm?**')

    testing_gateway.run_gateway(claws, bot, scenario)
    thread = next(iter(fake.threads))
    texts = [m.text for m in fake.in_thread(thread)]
    assert any(text.endswith('→ cancelled') for text in texts)
    assert any('interrupted' in text for text in texts)
    assert 'gateway stopping' in fake.notices()
