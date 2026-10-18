import contextlib
import json
import pathlib
import subprocess
import time
from collections.abc import Callable
from collections.abc import Generator

import pytest

from testing import commands
from testing import harness

REGISTRY = harness.REPO / 'pi' / 'extensions' / 'session-registry'
POLL_INTERVAL = 0.1


@contextlib.contextmanager
def rpc_pi(pi: harness.Pi) -> Generator[subprocess.Popen[bytes]]:
    """A long-lived pi session in RPC mode, which the registry serves."""
    with subprocess.Popen(
        ('pi', '--mode', 'rpc'),
        cwd=pi.cwd,
        env=pi.env,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as proc:
        try:
            yield proc
        finally:
            proc.kill()


def listed_names() -> set[str]:
    result = commands.bakery('list')
    assert result.returncode == 0, result.stderr
    return {line.split()[0] for line in result.stdout.splitlines()}


def wait_for_names(expected: set[str]) -> None:
    deadline = time.monotonic() + harness.WAIT_TIMEOUT
    names = listed_names()
    while names != expected and time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        names = listed_names()
    print(f'listed {names}, expected {expected}')
    assert names == expected


def test_unnamed_sessions_take_the_lowest_free_directory_name(
    run_pi: Callable[[pathlib.Path], harness.Pi],
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profile = tmp_path / 'profile'
    profile.mkdir()
    settings = {'extensions': [str(REGISTRY)]}
    (profile / 'settings.json').write_text(json.dumps(settings))
    pi = run_pi(profile)
    # The CLI lists this test's registry, and no claws.
    for name in ('PI_CODING_AGENT_STATE_DIR', 'XDG_STATE_HOME'):
        monkeypatch.setenv(name, pi.env[name])
    base = pi.cwd.name

    with contextlib.ExitStack() as stack:
        stack.enter_context(rpc_pi(pi))
        wait_for_names({base})
        crashed = stack.enter_context(rpc_pi(pi))
        wait_for_names({base, f'{base}-1'})
        stack.enter_context(rpc_pi(pi))
        wait_for_names({base, f'{base}-1', f'{base}-2'})

        # A crash leaves its socket and alias behind; the name is free again.
        crashed.kill()
        crashed.wait()
        wait_for_names({base, f'{base}-2'})
        stack.enter_context(rpc_pi(pi))
        wait_for_names({base, f'{base}-1', f'{base}-2'})
