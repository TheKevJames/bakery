import asyncio
import pathlib
import threading
from collections.abc import Awaitable
from collections.abc import Callable

from bakery.chat import relay
from bakery.chat import transport
from bakery.gateway import core
from testing import chat
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks

ASK = 'pi/claw-extensions/ask-operator.ts'


def run(
    claws: pathlib.Path,
    scenario: Callable[
        [core.Gateway, relay.Relay, chat.FakeTransport], Awaitable[None]
    ],
) -> None:
    fake = chat.FakeTransport()
    bot = relay.Relay(fake, chat.OPERATOR)

    async def wrapped(gateway: core.Gateway) -> None:
        await scenario(gateway, bot, fake)

    testing_gateway.run_gateway(claws, bot, wrapped)


def test_gateway_runs_get_a_thread_with_status_and_reply(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'scout': ''})
    long_reply = '\n'.join(f'line {i} ' + 'x' * 300 for i in range(40))
    fake_llm.queue(
        harness.Reply(tool='bash', args={'command': 'true'}),
        harness.Reply(text='all done'),
    )
    fake_llm.queue(harness.Reply(text=long_reply))

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
        [attachment] = [
            a for m in fake.in_thread(thread) if (a := m.attachment)
        ]
        assert attachment.data.decode() == long_reply
        # Both runs of the unit share its one thread.
        assert len(fake.threads) == 1

    run(claws, scenario)


def test_deleted_threads_and_status_messages_are_replaced(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'scout': ''})
    release = threading.Event()
    fake_llm.queue(
        harness.Reply(text='first', hold=release), harness.Reply(text='second')
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        del bot
        key = gateway.trigger('scout', None, 'go')
        await asyncio.to_thread(fake_llm.wait_requests, 1)
        first = fake.thread_named(key)
        status = await fake.message(first, '⏳')
        [status_id] = [i for i, m in fake.messages.items() if m is status]
        fake.delete_message(status_id)
        release.set()
        await fake.message(first, '✅')

        fake.delete_thread(first)
        gateway.message('scout', key, 'again')
        await fake.until(lambda: gateway.runs.thread_of(key) != first)
        second = gateway.runs.thread_of(key)
        assert second is not None
        await fake.message(second, 'second')
        await fake.message(second, '✅')
        await fake.until(
            lambda: any('thread was gone' in n for n in fake.notices())
        )
        print(fake.notices())
        renewed = [n for n in fake.notices() if 'thread was gone' in n]
        assert renewed == [f'{key}: its thread was gone; started a new one']

    run(claws, scenario)


def test_operator_messages_start_and_continue_threads(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'triage': ''})
    fake_llm.queue(
        harness.Reply(text='first answer'), harness.Reply(text='second answer')
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        await bot.on_message(chat.STRANGER, 'triage', None, 500, 'hi')
        assert not fake.threads

        await bot.on_message(
            chat.OPERATOR, 'triage', None, 501, 'look at #151'
        )
        [(thread, info)] = fake.threads.items()
        assert (info.name, info.from_message) == ('look at #151', 501)
        await fake.message(thread, 'first answer')
        assert 'look at #151' in fake_llm.transcript()

        await bot.on_message(chat.OPERATOR, 'triage', thread, 502, 'and #152?')
        await fake.message(thread, 'second answer')
        assert 'and #152?' in fake_llm.transcript()
        assert fake.messages[502].reactions == ['👀']
        assert gateway.runs.work_key_of_thread(thread) == (
            f'triage/thread-{thread}'
        )

        await bot.on_message(chat.OPERATOR, 'triage', 9999, 503, 'not ours')
        assert 503 not in fake.messages

    run(claws, scenario)


def test_messages_steer_in_flight_runs(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(root, {'build': ''})
    fake_llm.queue(
        harness.Reply(tool='bash', args={'command': 'sleep 1'}),
        harness.Reply(text='done'),
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        del gateway
        await bot.on_message(chat.OPERATOR, 'build', None, 600, 'start')
        [thread] = fake.threads
        await asyncio.to_thread(fake_llm.wait_requests, 1)
        await bot.on_message(chat.OPERATOR, 'build', thread, 601, 'use tabs')
        await fake.message(thread, '✅')
        assert 'use tabs' in fake_llm.transcript()
        # One run: the message joined it rather than queueing another.
        assert len(fake_llm.requests) == 2
        assert (await testing_gateway.claw_status('build'))['queued'] == []

    run(claws, scenario)


def test_ask_operator_hands_the_task_over_without_a_mention(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    testing_tasks.task('add', '-n', 'bakery', '--tag', 'triage', 'a task')
    claws = testing_gateway.make_claws(
        root,
        {'triage': ''},
        extensions={'triage': [ASK]},
        rules={'triage': 'triage'},
    )
    questions = [
        {'question': 'Which repo?', 'context': 'Two match.', 'default': 'all'},
        {'question': 'Now?', 'context': 'It is\nlate.', 'default': 'later'},
    ]
    fake_llm.queue(
        harness.call('ask_operator', id=1, questions=questions),
        harness.Reply(text='kept going'),
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        del bot
        key = gateway.trigger('triage', None, 'go')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        status = await fake.message(thread, '✅')
        assert 'settled - asked the operator on bakery:1' in status.text
        reply = await fake.message(thread, '→ bakery:1 is in `human`.')
        print(reply.text)
        assert '1. Which repo?\n   **Context:** Two match.\n' in reply.text
        assert '2. Now?\n   **Context:** It is\n   late.\n' in reply.text
        assert '   **Default:** later\n' in reply.text
        texts = [m.text for m in fake.in_thread(thread)]
        assert not [t for t in texts if f'<@{chat.OPERATOR}>' in t], texts
        # The tool ends the run: no model call after it.
        assert len(fake_llm.requests) == 1

    run(claws, scenario)
    task = testing_tasks.show('bakery:1')
    assert (task['tag'], task['claim']) == ('human', None)
    description = str(task['description'])
    assert description.startswith('## Questions (')
    assert '1. Which repo?' in description


def test_parked_runs_resume_from_their_button(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'build': '[limits]\nturns = 1\n'}
    )
    fake_llm.queue(
        harness.call('bash', command='true'), harness.Reply(text='resumed')
    )

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        key = gateway.trigger('build', None, 'go')
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(key)
        parked = await fake.message(thread, f'<@{chat.OPERATOR}> {key} parked')
        assert 'turn limit reached (1)' in parked.text
        resume = parked.button('Resume').custom_id

        refused = await bot.on_component(chat.STRANGER, resume)
        assert refused == relay.NOT_OPERATOR
        assert await bot.on_component(chat.OPERATOR, resume) == (
            f'Resuming {key}.'
        )
        await fake.message(thread, 'resumed')
        expired = await bot.on_component(chat.OPERATOR, 'bakery:resume:999')
        assert expired == relay.EXPIRED

    run(claws, scenario)


def test_silent_jobs_only_post_when_they_have_news(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '[concurrency]\nmax_active = 1\n'
               '[[job]]\nname = "beat"\ncron = "0 * * * *"\n'
               'prompt = "check"\nsilent_ok = true\n'}
    )  # fmt: skip
    fake_llm.queue(harness.Reply(text='NO_REPLY'), harness.Reply(text='news!'))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        del bot
        # One at a time: the quiet run is over by the time the loud one posts.
        gateway.trigger('a', 'beat', None)
        loud = gateway.trigger('a', 'beat', None)
        await fake.until(lambda: fake.threads)
        thread = fake.thread_named(loud)
        await fake.message(thread, 'news!')
        assert len(fake.threads) == 1

    run(claws, scenario)


def test_commands(root: pathlib.Path, fake_llm: harness.FakeLLM) -> None:
    claws = testing_gateway.make_claws(
        root,
        {'scout': '[limits]\ndaily_cost_usd = 1.0\n'
                  '[concurrency]\nmax_active = 1\n'},
    )  # fmt: skip
    fake_llm.queue(harness.Reply(prompt_tokens=2))

    async def scenario(
        gateway: core.Gateway, bot: relay.Relay, fake: chat.FakeTransport
    ) -> None:
        refused = await bot.on_command(chat.STRANGER, 'status', {})
        assert refused == transport.Response(relay.NOT_OPERATOR, private=True)
        error = await bot.on_command(
            chat.OPERATOR, 'trigger', {'claw': 'nope'}
        )
        assert error == transport.Response(
            '⚠️ unknown claw: nope', private=True
        )

        await bot.on_command(chat.OPERATOR, 'pause', {'target': 'scout'})
        status = await bot.on_command(chat.OPERATOR, 'status', {})
        assert 'scout: paused' in status.text
        await bot.on_command(chat.OPERATOR, 'resume', {'target': 'scout'})

        first = await bot.on_command(
            chat.OPERATOR, 'trigger', {'claw': 'scout', 'prompt': 'one'}
        )
        assert first.text.startswith('queued ')
        # One at a time: the second run starts once the first has paid.
        gateway.trigger('scout', None, 'two')
        await fake.until(
            lambda: 'scout has exhausted its daily budget' in fake.notices()
        )
        budget = await bot.on_command(chat.OPERATOR, 'budget', {})
        assert '$2.00 / $1.00 today' in budget.text

    run(claws, scenario)
