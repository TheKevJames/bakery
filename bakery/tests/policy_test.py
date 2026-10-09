import asyncio
import json
import pathlib
import sys
from collections.abc import Iterator

from bakery.gateway import channel
from bakery.gateway import core
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply
# Denials read differently per platform (macOS: Operation not permitted;
# Linux: the path is hidden or read-only), so assert on what got through.
FAILED = 'Command exited with code'


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


def test_file_access_follows_the_policy(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    secret = root / 'secret'
    secret.mkdir()
    (secret / 'key').write_text('hunter2')
    (root / 'dropins').mkdir()
    (root / 'dropins' / 'nobackup-x.zsh').write_text('hunter3')
    (root / 'dropins' / 'ok.zsh').write_text('fine')
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
                tools = ["read", "grep", "write", "bash"]
                write_paths = {toml_list([out])}
                deny_read = {toml_list([secret, root / 'dropins/nobackup-*'])}
                allow_read = {toml_list([state / 'b'])}
            """
        },
    )
    calls = [
        call('read', path=str(secret / 'key')),
        call('bash', command=f'cat {secret}/key'),
        call('read', path=str(root / 'dropins/nobackup-x.zsh')),
        call('bash', command=f'cat {root}/dropins/nobackup-x.zsh'),
        call('read', path=str(root / 'dropins/ok.zsh')),
        call('read', path=str(state / 'c/MEMORY.md')),
        call('read', path=str(state / 'a/MEMORY.md')),
        call('grep', pattern='hunter', path=str(root)),
        call('grep', pattern='memory', path=str(state / 'a')),
        call('write', path=str(out / 'w.txt'), content='w'),
        call('write', path=str(root / 'w.txt'), content='w'),
        call('bash', command=f'echo b > {out}/b.txt && cat {out}/b.txt'),
        call('bash', command=f'echo b > {root}/b.txt'),
        call('edit', path=str(out / 'w.txt'), edits=[]),
        call('read', path=str(state / 'b/MEMORY.md')),
        call('bash', command=f'cat {state}/b/MEMORY.md {state}/c/MEMORY.md'),
    ]

    run, results = run_calls(claws, fake_llm, 'a', calls)

    assert run.status == 'settled', run.reason
    assert 'is protected and cannot be read' in results[0]
    assert FAILED in results[1]
    assert 'hunter2' not in results[1]
    if sys.platform == 'darwin':
        assert f'file-read-data {secret.resolve()}/key' in results[1]
    assert 'is protected and cannot be read' in results[2]
    assert FAILED in results[3]
    assert 'hunter3' not in results[3]
    assert 'fine' in results[4]
    assert 'is protected and cannot be read' in results[5]
    assert 'memory of a' in results[6]
    assert 'contains protected paths' in results[7]
    assert 'memory of a' in results[8]
    assert (out / 'w.txt').read_text() == 'w'
    assert 'is not writable' in results[10]
    assert results[11].strip().endswith('b')
    assert FAILED in results[12]
    # Undeclared, so pi itself refuses it; the policy would block it too.
    assert 'Tool edit not found' in results[13]
    assert 'memory of b' in results[14]
    assert 'memory of b' in results[15]
    assert 'memory of c' not in results[15]
    assert not (root / 'w.txt').exists()
    assert not (root / 'b.txt').exists()


def test_bash_network_is_allowlisted(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': '[policy]\ntools = ["bash"]\nnetwork = ["example.com"]\n'}
    )
    calls = [
        call('bash', command='curl -sS -m 10 https://github.com -o /dev/null')
    ]

    run, results = run_calls(claws, fake_llm, 'a', calls)

    assert run.status == 'settled', run.reason
    assert '403' in results[0]
    assert 'deny network-outbound github.com:443' in results[0]
    assert 'sandbox policy blocked' in results[0]


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
