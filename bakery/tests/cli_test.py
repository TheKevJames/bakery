import asyncio
import pathlib

import pytest

from bakery.gateway import core
from testing import commands
from testing import gateway as testing_gateway
from testing import harness


def test_bakery_cli_sees_claw_sessions(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claws = testing_gateway.make_claws(
        root,
        {'scout': ''},
        extensions={
            'scout': [
                'pi/extensions/bakery.ts',
                'pi/extensions/session-registry',
            ]
        },
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(harness.Reply(text='claw reply'))
    # No interactive sessions in the way.
    monkeypatch.setenv('PI_CODING_AGENT_STATE_DIR', str(root / 'mine'))

    def bakery(*args: str) -> str:
        result = commands.bakery(*args)
        assert result.returncode == 0, result.stderr
        return result.stdout

    async def scenario(gateway: core.Gateway) -> None:
        key = gateway.trigger('scout', None, 'go')
        await fake.wait(key)
        session = key.replace('/', '-')
        # The child stays warm after the run, so its socket is live.
        listed = await asyncio.to_thread(bakery, 'list')
        assert session in listed
        assert 'idle' in listed
        assert (await asyncio.to_thread(bakery, 'read', session)).strip() == (
            'claw reply'
        )

    testing_gateway.run_gateway(claws, fake, scenario)
