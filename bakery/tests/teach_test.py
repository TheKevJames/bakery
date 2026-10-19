import asyncio
import pathlib
import subprocess

from bakery.gateway import core
from bakery.gateway import prompts
from testing import commands
from testing import gateway as testing_gateway
from testing import harness

# defaults.toml's learn job, on the claw's (fake) model.
LEARN = """
[[job]]
name = "learn"
prompt = "/learn"
"""
EXTENSIONS = ['pi/claw-extensions/memory']
# Quotes and spacing pi's template arguments would have mangled.
LESSON = 'Your sandbox now allows heredocs; check "/tmp" too.\n  It\'s fine.'


def user_prompt(fake_llm: harness.FakeLLM, index: int) -> str:
    messages = fake_llm.requests[index]['messages']
    assert isinstance(messages, list)
    content = next(m for m in messages if m['role'] == 'user')['content']
    assert isinstance(content, list)
    return ''.join(str(part['text']) for part in content)


def test_teaching_reports_the_reply_and_memory_diff(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    fake_llm.queue(
        harness.call('memory_edit', content='# scout\n\n- heredocs work\n'),
        harness.Reply(text='Verified heredocs; updated memory.'),
    )
    fake = testing_gateway.FakeChannel()
    taught: list[subprocess.CompletedProcess[str]] = []

    async def scenario(gateway: core.Gateway) -> None:
        gateway.pause('triage', abort=False)
        taught.append(
            await asyncio.to_thread(commands.bakery, 'teach', 'all', LESSON)
        )

    testing_gateway.run_gateway(
        testing_gateway.make_claws(
            root,
            {'scout': LEARN, 'triage': LEARN},
            extensions={'scout': EXTENSIONS, 'triage': EXTENSIONS},
        ),
        fake,
        scenario,
    )

    assert len(taught) == 1
    result = taught[0]
    assert result.returncode == 0, result.stderr
    assert result.stderr == 'skipped triage: paused\n'
    out = result.stdout
    assert out.startswith('teaching scout: scout/learn-')
    assert ': settled\nVerified heredocs; updated memory.\n\n' in out
    assert '+++ b/scout/MEMORY.md\n' in out
    assert '+- heredocs work\n' in out
    assert user_prompt(fake_llm, 0) == (
        f'{prompts.expand("/learn")}\n\n<lesson>\n{LESSON}\n</lesson>'
    )
    assert len(fake_llm.requests) == 2


def test_teaching_refuses_what_cannot_run(root: pathlib.Path) -> None:
    fake = testing_gateway.FakeChannel()
    refused: list[str] = []

    async def scenario(gateway: core.Gateway) -> None:
        gateway.pause('scout', abort=False)
        for args in (('scout', 'x'), ('nobody', 'x'), ('scout', ' ')):
            ran = await asyncio.to_thread(commands.bakery, 'teach', *args)
            assert ran.returncode == 1
            refused.append(ran.stderr.strip())

    testing_gateway.run_gateway(
        testing_gateway.make_claws(root, {'scout': LEARN}), fake, scenario
    )

    assert refused[0] == 'scout is paused'
    assert 'nobody' in refused[1]
    assert refused[2] == 'the lesson is empty'
