import datetime
import pathlib

from bakery.gateway import core
from testing import commands
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks

MEMORY = 'pi/claw-extensions/memory'
DREAM = """
[[job]]
name = "dream"
cron = "0 3 * * *"
prompt = "/dream"
silent_ok = true
"""


def state_dir(root: pathlib.Path, claw: str = 'a') -> pathlib.Path:
    return root / 'state' / 'claws' / claw


def today_note(root: pathlib.Path) -> pathlib.Path:
    day = datetime.date.today().isoformat()
    return state_dir(root) / 'memory' / f'{day}.md'


def git_log(root: pathlib.Path) -> str:
    return commands.git(root / 'state' / 'claws', 'log', '--format=%s')


def test_memory_append_records_sources(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': ''}, extensions={'a': [MEMORY]}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.call(
            'memory_append', text='Operator prefers tabs', source='operator'
        ),
        harness.call(
            'memory_append', text='Issue says use spaces', source='external'
        ),
        harness.Reply(text='done'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('a', None, 'go'))
        assert run.status == 'settled', run.reason

    testing_gateway.run_gateway(claws, fake, scenario)
    note = today_note(root).read_text()
    assert '· operator\n\nOperator prefers tabs' in note
    assert '· external\n\nIssue says use spaces' in note
    assert git_log(root).splitlines()[0].endswith(': settled')


def test_dream_job_curates_memory_and_keeps_its_settings_on_resume(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root,
        {'a': DREAM, 'b': ''},
        extensions={'a': [MEMORY]},
        gateway={'max_processes': 1},
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.call('memory_edit', content='# Memory\n\n- likes tabs\n'),
        harness.Reply(text='NO_REPLY'),
        harness.Reply(text='b done'),
        harness.call(
            'memory_edit',
            edits=[{'oldText': 'likes tabs', 'newText': 'likes tabs (v2)'}],
        ),
        harness.Reply(text='updated'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('a', 'dream', None)
        quiet = await fake.wait(key)
        assert quiet.status == 'settled', quiet.reason
        assert quiet.final_text is None
        assert 'time to consolidate your memory' in fake_llm.transcript()
        # A fresh child must still get the job's settings: b's run, with
        # room for one child, evicts a's.
        other = await fake.wait(gateway.trigger('b', None, 'go'))
        assert other.final_text == 'b done'
        gateway.message('a', key, 'one more thing')
        loud = await fake.wait(key)
        assert loud.final_text == 'updated'

    testing_gateway.run_gateway(claws, fake, scenario)
    memory = (state_dir(root) / 'MEMORY.md').read_text()
    assert memory == '# Memory\n\n- likes tabs (v2)\n'


def test_flush_before_compaction(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    # A margin past the whole context window: flush after the first turn.
    claws = testing_gateway.make_claws(
        root,
        {'a': '[memory]\nflush_margin_tokens = 1000000\n'},
        extensions={'a': [MEMORY]},
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.Reply(text='the answer'),
        harness.call('memory_append', text='flushed fact', source='self'),
        harness.Reply(text='NO_REPLY'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('a', None, 'go'))
        assert run.status == 'settled', run.reason
        assert run.final_text == 'the answer'

    testing_gateway.run_gateway(claws, fake, scenario)
    assert fake_llm.transcript().count('about to be compacted') == 1
    assert 'flushed fact' in today_note(root).read_text()


def test_operator_updates_are_filed_for_the_operator(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    testing_tasks.create(root / 'tasks')
    claws = testing_gateway.make_claws(
        root, {'a': ''}, extensions={'a': [MEMORY]}
    )
    edit = [
        {'oldText': '## Preferences\n', 'newText': '## Preferences\n- tabs\n'}
    ]
    fake_llm.queue(
        harness.call('propose_operator_update', edits=edit, reason='said so'),
        harness.call(
            'propose_operator_update', edits=edit, reason='said so again'
        ),
        harness.call(
            'propose_operator_update',
            edits=[{'oldText': 'x', 'newText': 'y'}],
            reason='r',
        ),
        harness.Reply(text='done'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('a', None, 'go'))
        assert run.status == 'settled', run.reason

    testing_gateway.run_gateway(claws, fake, scenario)
    results = fake_llm.tool_results()
    assert 'Filed as task #bakery:1' in results[0]
    assert 'open task #bakery:1 already has' in results[1]
    assert 'text not found' in results[2]
    shown = testing_tasks.task('bakery:1', 'show')
    assert 'bakery > human' in shown
    assert '+ - tabs' in shown
    assert 'said so' in shown
    shared = root / 'state' / 'claws' / '_shared' / 'OPERATOR.md'
    assert '- tabs' not in shared.read_text()


def test_compaction_counts_towards_the_run_cost(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root,
        {'a': '[limits]\ncost_usd = 100\ndaily_cost_usd = 100\n'},
        gateway={'daily_cost_usd': 100},
        # Compact as soon as there is anything to summarize.
        settings={
            'a': {
                'compaction': {'reserveTokens': 199_990, 'keepRecentTokens': 0}
            }
        },
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(
        harness.Reply(tool='bash', args={'command': 'true'}, prompt_tokens=20),
        harness.Reply(text='the summary', prompt_tokens=7),
        harness.Reply(text='done'),
    )

    async def scenario(gateway: core.Gateway) -> None:
        run = await fake.wait(gateway.trigger('a', None, 'go'))
        assert run.status == 'settled', run.reason
        assert run.cost_usd == 27

    testing_gateway.run_gateway(claws, fake, scenario)
    assert 'the summary' in fake_llm.transcript()
