import argparse
import functools
import http.server
import json
import os
import pathlib
import socket
import sys
import threading
import urllib.parse

import pytest

from bakery.gateway import channel
from bakery.gateway import cli as gateway_cli
from bakery.gateway import core
from testing import commands
from testing import gateway as testing_gateway
from testing import harness
from testing import servers


def run_calls(
    claws: pathlib.Path,
    fake_llm: harness.FakeLLM,
    claw: str,
    calls: list[harness.Reply],
    fake: testing_gateway.FakeChannel | None = None,
) -> tuple[channel.RunInfo, list[str]]:
    """Run one unit whose model makes `calls`; returns the tool results."""
    fake = fake or testing_gateway.FakeChannel()
    fake_llm.queue(*calls, harness.Reply(text='done'))
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


def install_editable(
    venv: pathlib.Path, tool: str, source: pathlib.Path
) -> None:
    """A venv whose `tool` runs from `source`, as `uv pip install -e`."""
    (venv / 'bin').mkdir(parents=True)
    (venv / 'pyvenv.cfg').write_text(f'home = {venv / "bin"}\n')
    script = venv / 'bin' / tool
    script.write_text(f'#!/bin/sh\ncat {source}/code.py\n')
    script.chmod(0o755)
    python = f'python{sys.version_info.major}.{sys.version_info.minor}'
    dist = venv / 'lib' / python / 'site-packages' / 'x.dist-info'
    dist.mkdir(parents=True)
    (dist / 'direct_url.json').write_text(
        json.dumps({'url': source.as_uri(), 'dir_info': {'editable': True}})
    )


def ssh_keys(home: pathlib.Path, count: int) -> list[pathlib.Path]:
    """`count` private keys in `home`'s ~/.ssh, which claws never read."""
    (home / '.ssh').mkdir(parents=True)
    keys = [home / '.ssh' / f'key{i}' for i in range(count)]
    for key in keys:
        key.write_text('hunter2')
    return keys


def test_file_access_follows_the_policy(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = root / 'home'
    monkeypatch.setenv('HOME', str(home))
    keys = ssh_keys(home, 5)
    repo = home / 'src' / 'personal' / 'repo'
    repo.mkdir(parents=True)
    (repo / 'code.py').write_text('fine')
    (repo / '.env').write_text('hunter3')
    (repo / 'quote.md').write_text('cat: x: Operation not permitted')
    tool = home / 'src' / 'personal' / 'tool'
    tool.mkdir()
    (tool / 'code.py').write_text('editable')
    (tool.parent / 'other').write_text('hunter5')
    install_editable(root / 'venv', 'fd', tool)
    monkeypatch.setenv('PATH', f'{root / "venv" / "bin"}:{os.environ["PATH"]}')
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
                read_paths = [
                    "~/src/personal/repo", "{state / 'b'}", "{out}",
                ]
                deny_read = ["../*/auth.json", "~/**/.env*"]
                write_paths = {toml_list([out])}
            """,
            'b': '',
        },
    )
    (claws / 'a' / 'auth.json').write_text('{}')
    (claws / 'b' / 'auth.json').write_text('{"b": "hunter4"}')
    calls = [
        harness.call('read', path=str(keys[0])),
        # One process's denials reach the violation log in a single burst.
        harness.call('bash', command=f'cat {" ".join(map(str, keys))}'),
        harness.call('read', path=str(repo / 'code.py')),
        harness.call('read', path=str(repo / '.env')),
        harness.call('read', path=str(state / 'c' / 'MEMORY.md')),
        harness.call('read', path=str(state / 'a' / 'MEMORY.md')),
        harness.call('bash', command=f'cat {state}/b/MEMORY.md'),
        harness.call('read', path=str(claws / 'b' / 'auth.json')),
        harness.call('read', path=str(claws / 'a' / 'auth.json')),
        harness.call('write', path=str(out / 'w.txt'), content='w'),
        harness.call('write', path=str(root / 'w.txt'), content='w'),
        harness.call(
            'bash', command=f'echo b > {out}/b.txt && cat {out}/b.txt'
        ),
        harness.call('bash', command=f'echo b > {root}/b.txt'),
        # A runtime tool reads its editable source, but nothing beside it.
        harness.call('bash', command='fd'),
        harness.call('bash', command=f'cat {home}/src/personal/other'),
        # Succeeding, only quoting a refusal: no note.
        harness.call('read', path=str(repo / 'quote.md')),
        harness.call('bash', command=f'cat {repo}/quote.md'),
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
    assert 'editable' in results[13]
    for refused in (0, 1, 3, 4, 7, 10, 12, 14):
        assert 'sandbox policy blocked it' in results[refused], refused
    assert all('Operation not permitted' in r for r in results[15:])
    assert not any('sandbox policy blocked' in r for r in results[15:])
    assert not [
        key
        for key in keys
        if f'file-read-data {key.resolve()}' not in violations(root, 'a')
    ]


def test_network_refuses_only_other_unix_sockets(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    port = urllib.parse.urlsplit(fake_llm.url).port
    unix_path = root / 'u.sock'
    claws = testing_gateway.make_claws(root, {'a': ''})
    status = 'curl -sS -m 5 -o /dev/null -w "%{http_code}"'
    connect = (
        f"import socket; socket.socket(socket.AF_UNIX).connect('{unix_path}')"
    )
    calls = [
        harness.call('bash', command=f'{status} http://localhost:{port}/'),
        harness.call(
            'bash', command=f'{status} --noproxy "*" http://127.0.0.1:{port}/'
        ),
        harness.call('bash', command=f'python3 -c "{connect}"'),
        # A claw's only GitHub token is its GH_TOKEN (test claws have none);
        # the operator's, in the Keychain, stays unreachable.
        harness.call('bash', command='security default-keychain'),
        # Never resolves (RFC 6761): the proxy lets it through to fail
        # upstream (502) rather than refusing it (403).
        harness.call('bash', command='curl -sS -m 5 https://example.invalid'),
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
    assert 'A default keychain could not be found' in results[3]
    assert 'CONNECT tunnel failed, response 502' in results[4]
    logged = violations(root, 'a')
    assert 'localhost' not in logged
    assert 'example.invalid' not in logged


def _pdf(words: list[str]) -> bytes:
    """
    A one-page PDF showing `words`, spaced by positioning rather than space
    characters, as LaTeX does.
    """
    spaced = '-300'.join(f'({word})' for word in words)
    stream = f'BT /F1 12 Tf 72 720 Td [{spaced}] TJ ET'.encode()
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792]'
        b' /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
        b'<< /Length %d >>\nstream\n%s\nendstream' % (len(stream), stream),
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    ]
    pdf = b'%PDF-1.4\n'
    offsets = []
    for number, body in enumerate(objects, 1):
        offsets.append(len(pdf))
        pdf += b'%d 0 obj\n%s\nendobj\n' % (number, body)
    xref = len(pdf)
    pdf += b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1)
    pdf += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    pdf += b'trailer\n<< /Size %d /Root 1 0 R >>\n' % (len(objects) + 1)
    return pdf + b'startxref\n%d\n%%%%EOF\n' % xref


def test_web_fetch_makes_pages_readable_in_the_sandbox(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    site = root / 'site'
    site.mkdir()
    prose = ' '.join(['The sandboxed claw read this article.'] * 20)
    (site / 'page.html').write_text(
        '<html><head><title>Fixture</title></head><body>'
        '<nav><a href="/">Home</a></nav>'
        f'<main><article><h1>Fixture article</h1><p>{prose}</p>'
        '</article></main></body></html>'
    )
    (site / 'doc.pdf').write_bytes(
        _pdf(['Hello', 'from', 'the', 'fixture', 'PDF'])
    )
    claws = testing_gateway.make_claws(
        root, {'a': ''}, extensions={'a': ['pi/extensions/ketch.ts']}
    )
    handler = functools.partial(
        http.server.SimpleHTTPRequestHandler, directory=str(site)
    )
    with servers.LocalServer(('127.0.0.1', 0), handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = f'http://localhost:{server.server_port}'
        calls = [
            harness.call('web_fetch', url=f'{base}/page.html'),
            harness.call('web_fetch', url=f'{base}/doc.pdf'),
        ]
        run, results = run_calls(claws, fake_llm, 'a', calls)
        server.shutdown()

    assert run.status == 'settled', run.reason
    assert '# Fixture article' in results[0]
    assert 'The sandboxed claw read this article.' in results[0]
    assert 'Home' not in results[0]
    assert 'Hello from the fixture PDF' in results[1]


def test_claws_get_pi_default_tools_and_their_extensions_tools(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    claws = testing_gateway.make_claws(
        root, {'a': ''}, extensions={'a': ['pi/extensions/context7.ts']}
    )

    run, _ = run_calls(claws, fake_llm, 'a', [])

    assert run.status == 'settled', run.reason
    tools = fake_llm.requests[0]['tools']
    assert isinstance(tools, list)
    assert {tool['function']['name'] for tool in tools} == {
        'read', 'bash', 'edit', 'write', 'context7',
    }  # fmt: skip


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
    # Under /tmp, but outside the claw state root.
    stray = root / 'stray.txt'
    stray.write_text('swordfish')
    shared_tmp = pathlib.Path('/private/tmp/claude')
    shared_tmp.mkdir(exist_ok=True)
    planted = shared_tmp / f'{root.name}.txt'

    def run(*command: str) -> int:
        args = argparse.Namespace(
            claw='a', claws_dir=str(claws), command=['--', *command]
        )
        return gateway_cli.do_sandbox_exec(args)

    assert run('sh', '-c', 'echo "TMPDIR=$TMPDIR"') == 0
    assert run('cat', str(secret)) != 0
    assert run('cat', str(stray)) != 0
    assert run('touch', str(planted)) != 0
    out = capfd.readouterr()
    print(out.err)
    assert f'TMPDIR={root.resolve()}/state/claws/a/tmp/pi-' in out.out
    assert 'hunter2' not in out.out + out.err
    assert 'swordfish' not in out.out + out.err
    assert not planted.exists()


def test_claws_keep_their_toolchain_in_their_home(
    root: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture[str],
) -> None:
    home = root / 'home'
    monkeypatch.setenv('HOME', str(home))
    tool = home / 'src' / 'tool'
    tool.mkdir(parents=True)
    (tool / 'code.py').write_text('editable\n')
    install_editable(home / 'venvs' / 'lint', 'lint', tool)
    install_editable(home / 'venvs' / 'other', 'other', tool)
    monkeypatch.setenv(
        'PATH',
        f'{home / "venvs" / "lint" / "bin"}:'
        f'{home / "venvs" / "other" / "bin"}:{os.environ["PATH"]}',
    )
    claws = testing_gateway.make_claws(
        root, {'a': '[policy]\ncommands = ["lint"]'}
    )
    # As `uv tool install` and Go's module cache would, then a .vscode dir
    # in a repo, which stays protected.
    script = """
        lint && echo RAN-TOOL
        other || echo REFUSED-OTHER
        mkdir -p "$XDG_BIN_HOME" "$GOPATH/pkg/mod/m/.vscode" "$GOCACHE"
        printf '#!/bin/sh\\necho RAN-INSTALLED\\n' > "$XDG_BIN_HOME/installed"
        chmod +x "$XDG_BIN_HOME/installed" && installed
        echo {} > "$GOPATH/pkg/mod/m/.vscode/settings.json" && echo WROTE-CACHE
        mkdir -p "$BAKERY_MEMORY_DIR/worktrees/r/.vscode"
        echo {} > "$BAKERY_MEMORY_DIR/worktrees/r/.vscode/settings.json" \\
            || echo REFUSED-REPO
        echo "GO=$GOPATH:$GOCACHE"
    """
    args = argparse.Namespace(
        claw='a', claws_dir=str(claws), command=['sh', '-c', script]
    )

    gateway_cli.do_sandbox_exec(args)

    out = capfd.readouterr()
    print(out.out, out.err)
    for marker in (
        'editable', 'RAN-TOOL', 'REFUSED-OTHER', 'RAN-INSTALLED',
        'WROTE-CACHE', 'REFUSED-REPO',
    ):  # fmt: skip
        assert marker in out.out.split(), marker
    claw_home = root.resolve() / 'state' / 'claws' / '_homes' / 'a'
    assert f'GO={claw_home}/data/go:{claw_home}/cache/go-build' in out.out


def test_claws_change_repos_only_in_worktrees(
    root: pathlib.Path, capfd: pytest.CaptureFixture[str]
) -> None:
    upstream = root / 'remote' / 'o' / 'r.git'
    commands.git(root, 'init', '-q', '--bare', '-b', 'main', str(upstream))
    seed = root / 'seed'
    commands.git(root, 'init', '-q', '-b', 'main', str(seed))
    (seed / 'app.py').write_text('pass\n')
    commands.git(seed, 'add', '.')
    commands.git(
        seed,
        '-c', 'user.name=t', '-c', 'user.email=t@t',
        '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'init',
    )  # fmt: skip
    commands.git(seed, 'push', '-q', str(upstream), 'main')
    claws = testing_gateway.make_claws(
        root,
        {
            'a': f"""
                [policy]
                read_paths = {toml_list([upstream])}
                write_paths = {toml_list([upstream])}
            """
        },
    )
    # As pi/claw-rules/repos.md has every claw work; each refusal is echoed.
    script = f"""
        clone="$BAKERY_MEMORY_DIR/clones/o/r.git"
        git init -q --bare "$clone"
        git -C "$clone" remote add origin {upstream}
        git -C "$clone" fetch -q --prune origin
        git -C "$clone" remote set-head origin --auto
        wt="$BAKERY_MEMORY_DIR/worktrees/o/r/$BAKERY_SESSION_ID"
        x=kjames/bakery-x
        git -C "$clone" worktree add -q -b "$x" "$wt" origin/HEAD
        cd "$wt" && echo x > x && git add x
        git commit -qm x && echo COMMITTED
        git push -q origin "HEAD:$x" && echo PUSHED
        git push -q origin HEAD:main || echo REFUSED-MAIN
        git tag v1 && git push -q origin v1 || echo REFUSED-TAG
        git -C "$clone" push -q origin "$x:kjames/bakery-z" || echo REFUSED-BARE
        out="$TMPDIR/elsewhere"
        git -C "$clone" worktree add -q -b kjames/bakery-y "$out" origin/HEAD
        cd "$out" && echo y > y && git add y
        git commit -qm y || echo REFUSED-OUTSIDE
        plain="$BAKERY_MEMORY_DIR/clones/o/plain"
        git clone -q {upstream} "$plain" && cd "$plain" && echo z > z
        git add z && git commit -qm z || echo REFUSED-CLONE
    """
    args = argparse.Namespace(
        claw='a', claws_dir=str(claws), command=['sh', '-c', script]
    )

    gateway_cli.do_sandbox_exec(args)

    out = capfd.readouterr()
    print(out.out, out.err)
    for marker in (
        'COMMITTED', 'PUSHED', 'REFUSED-MAIN', 'REFUSED-TAG', 'REFUSED-BARE',
        'REFUSED-OUTSIDE', 'REFUSED-CLONE',
    ):  # fmt: skip
        assert marker in out.out.split(), marker
    assert 'bakery: push only kjames/bakery-* branches' in out.err
    refs = commands.git(upstream, 'for-each-ref', '--format=%(refname)')
    assert refs.split() == ['refs/heads/kjames/bakery-x', 'refs/heads/main']
