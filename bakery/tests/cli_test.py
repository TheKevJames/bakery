import asyncio
import pathlib
import subprocess
import sys

import pytest

from bakery.gateway import core
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply


def test_bakery_cli_sees_claw_sessions(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    claws = testing_gateway.make_claws(
        root, {'scout': ''}, extensions={'scout': ['pi/extensions/bakery.ts']}
    )
    fake = testing_gateway.FakeChannel()
    fake_llm.queue(Reply(text='claw reply'))
    # No interactive sessions in the way.
    monkeypatch.setenv('PI_CODING_AGENT_BAKERY_DIR', str(root / 'mine'))

    def bakery(*args: str) -> str:
        result = subprocess.run(
            (sys.executable, '-m', 'bakery.cli', *args),
            capture_output=True,
            text=True,
            check=True,
        )
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
