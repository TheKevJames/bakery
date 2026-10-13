import argparse
import asyncio
import json
import pathlib
import socket
import urllib.parse
from collections.abc import Iterator

import pytest

from bakery.gateway import channel
from bakery.gateway import cli as gateway_cli
from bakery.gateway import core
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply


def call(tool: str, **args: object) -> harness.Reply:
    return Reply(tool=tool, args=args)


def run_calls(
    claws: pathlib.Path,
    fake_llm: harness.FakeLLM,
    claw: str,
    calls: list[harness.Reply],
    fake: testing_gateway.FakeChannel | None = None,
) -> tuple[channel.RunInfo, list[str]]:
    """Run one unit whose model makes `calls`; returns the tool results."""
    fake = fake or testing_gateway.FakeChannel()
    fake_llm.queue(*calls, Reply(text='done'))
    done: list[channel.RunInfo] = []

    async def scenario(gateway: core.Gateway) -> None:
        done.append(await fake.wait(gateway.trigger(claw, None, 'go')))

    testing_gateway.run_gateway(claws, fake, scenario)
    return done[0], fake_llm.tool_results() if fake_llm.requests else []


def toml_list(paths: list[pathlib.Path | str]) -> str:
    return json.dumps([str(p) for p in paths])


def violations(root: pathlib.Path, claw: str) -> str:
    logs = root / 'state' / 'claws' / '_gateway' / 'violations' / claw
    return ''.join(p.read_text() for p in logs.glob('*.jsonl'))


def test_file_access_follows_the_policy(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = root / 'home'
    monkeypatch.setenv('HOME', str(home))
    (home / '.ssh').mkdir(parents=True)
    (home / '.ssh' / 'key').write_text('hunter2')
    repo = home / 'src' / 'personal' / 'repo'
    repo.mkdir(parents=True)
    (repo / 'code.py').write_text('fine')
    (repo / '.env').write_text('hunter3')
    out = root / 'out'
    out.mkdir()
    state = root / 'state' / 'claws'
    for claw in ('a', 'b', 'c'):
        (state / claw).mkdir(parents=True)
        (state / claw / 'MEMORY.md').write_text(f'memory of {claw}')
    claws = testing_gateway.make_claws(
        root,
        {
            'a': f"""
                [policy]
                tools = ["read", "write", "bash"]
                read_paths = {toml_list(['~/src/personal', state / 'b'])}
                write_paths = {toml_list([out])}
            """,
            'b': '',
        },
    )
    (claws / 'a' / 'auth.json').write_text('{}')
    (claws / 'b' / 'auth.json').write_text('{"b": "hunter4"}')
    calls = [
        call('read', path=str(home / '.ssh' / 'key')),
        call('bash', command=f'cat {home}/.ssh/key'),
        call('read', path=str(repo / 'code.py')),
        call('read', path=str(repo / '.env')),
        call('read', path=str(state / 'c' / 'MEMORY.md')),
        call('read', path=str(state / 'a' / 'MEMORY.md')),
        call('bash', command=f'cat {state}/b/MEMORY.md'),
        call('read', path=str(claws / 'b' / 'auth.json')),
        call('read', path=str(claws / 'a' / 'auth.json')),
        call('write', path=str(out / 'w.txt'), content='w'),
        call('write', path=str(root / 'w.txt'), content='w'),
        call('bash', command=f'echo b > {out}/b.txt && cat {out}/b.txt'),
        call('bash', command=f'echo b > {root}/b.txt'),
    ]

    run, results = run_calls(claws, fake_llm, 'a', calls)

    assert run.status == 'settled', run.reason
    assert not any('hunter' in result for result in results)
    assert 'memory of c' not in results[4]
    assert 'fine' in results[2]
    assert 'memory of a' in results[5]
    assert 'memory of b' in results[6]
    assert '{}' in results[8]
    assert (out / 'w.txt').read_text() == 'w'
    assert results[11].strip().endswith('b')
    assert not (root / 'w.txt').exists()
    assert not (root / 'b.txt').exists()
    for refused in (0, 1, 3, 4, 7, 10, 12):
        assert 'sandbox policy blocked it' in results[refused], refused
    assert f'file-read-data {home.resolve()}/.ssh/key' in violations(root, 'a')


def test_network_refuses_only_other_unix_sockets(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    port = urllib.parse.urlsplit(fake_llm.url).port
    unix_path = root / 'u.sock'
    claws = testing_gateway.make_claws(
        root, {'a': '[policy]\ntools = ["bash"]\n'}
    )
    status = 'curl -sS -m 5 -o /dev/null -w "%{http_code}"'
    connect = (
        f"import socket; socket.socket(socket.AF_UNIX).connect('{unix_path}')"
    )
    calls = [
        call('bash', command=f'{status} http://localhost:{port}/'),
        call(
            'bash', command=f'{status} --noproxy "*" http://127.0.0.1:{port}/'
        ),
        call('bash', command=f'python3 -c "{connect}"'),
        # A claw's only GitHub token is its GH_TOKEN (test claws have none);
        # mine, in the Keychain, stays unreachable.
        call('bash', command='gh auth token'),
        # Offline, this fails upstream rather than at the sandbox.
        call('bash', command=f'{status} https://example.com'),
    ]

    with socket.socket(socket.AF_UNIX) as unix_server:
        unix_server.bind(str(unix_path))
        unix_server.listen()
        run, results = run_calls(claws, fake_llm, 'a', calls)

    assert run.status == 'settled', run.reason
    # Through the proxy, then directly: FakeLLM answers GETs with 501.
    assert results[0].strip() == '501'
    assert results[1].strip() == '501'
    assert 'sandbox policy blocked it' in results[2]
    assert 'no oauth token' in results[3]
    assert 'sandbox policy blocked it' not in results[4]
    logged = violations(root, 'a')
    assert 'localhost' not in logged
    assert 'example.com' not in logged


def test_confirm_patterns_ask_first(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '[policy]\ntools = ["bash"]\nconfirm = ["^rm "]\n'}
    )
    answers: Iterator[dict[str, object]] = iter(
        [{'confirmed': False}, {'confirmed': True}]
    )

    async def answer(
        info: channel.RunInfo, request: dict[str, object]
    ) -> dict[str, object]:
        del info, request
        await asyncio.sleep(0)
        return next(answers)

    fake = testing_gateway.FakeChannel(dialogs=answer)
    calls = [
        call('bash', command='rm -f /tmp/bakery-nonexistent'),
        call('bash', command='rm -f /tmp/bakery-nonexistent && echo removed'),
        call('bash', command='echo unasked'),
    ]

    run, results = run_calls(claws, fake_llm, 'a', calls, fake)

    assert run.status == 'settled', run.reason
    assert [r['message'] for r in fake.asked] == [
        'rm -f /tmp/bakery-nonexistent',
        'rm -f /tmp/bakery-nonexistent && echo removed',
    ]
    assert 'denied by the user' in results[0]
    assert 'removed' in results[1]
    assert 'unasked' in results[2]


def test_startup_failures_fail_the_run(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': ''}, extensions={'a': ['faulty_start.ts']}
    )

    run, _ = run_calls(claws, fake_llm, 'a', [])

    assert run.status == 'failed'
    assert 'broken at startup' in (run.reason or '')
    assert not fake_llm.requests


def test_sandbox_exec_runs_commands_as_the_claw(
    root: pathlib.Path, capfd: pytest.CaptureFixture[str]
) -> None:
    claws = testing_gateway.make_claws(root, {'a': ''})
    secret = root / 'state' / 'claws' / 'b' / 'MEMORY.md'
    secret.parent.mkdir(parents=True)
    secret.write_text('hunter2')

    def run(*command: str) -> int:
        args = argparse.Namespace(
            claw='a', claws_dir=str(claws), command=['--', *command]
        )
        return gateway_cli.do_sandbox_exec(args)

    assert run('sh', '-c', 'echo "TMPDIR=$TMPDIR"') == 0
    assert run('cat', str(secret)) != 0
    out = capfd.readouterr()
    assert f'TMPDIR={root.resolve()}/state/claws/a/tmp/pi-' in out.out
    assert 'hunter2' not in out.out + out.err
