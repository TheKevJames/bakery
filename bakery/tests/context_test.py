import json
import pathlib
import tomllib
from collections.abc import Callable

import pytest

from bakery import state
from testing import harness

INTERACTIVE = harness.REPO / 'interactive'


def make_profile(root: pathlib.Path, context_toml: str) -> pathlib.Path:
    profile = root / 'profile'
    profile.mkdir()
    settings = {
        'extensions': [str(harness.REPO / 'pi' / 'extensions' / 'context')]
    }
    (profile / 'settings.json').write_text(json.dumps(settings))
    (profile / 'context.toml').write_text(context_toml)
    return profile


def test_interactive_profile_injects_every_rule_and_user(
    run_pi: Callable[[pathlib.Path], harness.Pi],
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pi = run_pi(INTERACTIVE)
    monkeypatch.setenv('XDG_STATE_HOME', pi.env['XDG_STATE_HOME'])
    state.init()
    state.user_file().write_text('# About the user\nprefers tabs? never\n')

    assert pi.prompt('hi').strip() == 'OK'

    config = tomllib.loads((INTERACTIVE / 'context.toml').read_text())
    rules = [
        (INTERACTIVE / f['path']).read_text().strip()
        for f in config['file']
        if f['path'].startswith('../pi/rules/')
    ]
    assert len(rules) == len(list((harness.REPO / 'pi' / 'rules').iterdir()))
    system = fake_llm.system_prompt()
    positions = [system.index(rule) for rule in rules]
    assert positions == sorted(positions)
    assert 'prefers tabs? never' in system


def test_every_turn_reread_and_session_start_once(
    tmp_path: pathlib.Path,
    run_pi: Callable[[pathlib.Path], harness.Pi],
    fake_llm: harness.FakeLLM,
) -> None:
    rules = tmp_path / 'rules.md'
    memory = tmp_path / 'memory'
    memory.mkdir()
    for day in ('2026-09-30', '2026-10-01', '2026-10-02'):
        (memory / f'{day}.md').write_text(f'note from {day}')
    rules.write_text('rule v1 ' + 'x' * 100)
    profile = make_profile(
        tmp_path,
        f'''
[[file]]
path = "{rules}"
max_chars = 20

[[file]]
path = "{memory}/*.md"
latest = 2
inject = "session_start"
''',
    )
    pi = run_pi(profile)

    pi.prompt('first')
    first = fake_llm.transcript()
    assert 'rule v1 xxxxxxxxxxxx\n\n[truncated: showing 20 of 108' in (
        fake_llm.system_prompt()
    )
    assert 'note from 2026-09-30' not in first
    assert first.count('note from 2026-10-01') == 1
    assert first.count('note from 2026-10-02') == 1

    rules.write_text('rule v2')
    (memory / '2026-10-02.md').write_text('rewritten note')
    pi.prompt('second', resume=True)

    assert 'rule v2' in fake_llm.system_prompt()
    assert 'rule v1' not in fake_llm.system_prompt()
    second = fake_llm.transcript()
    assert second.count('note from 2026-10-02') == 1
    assert 'rewritten note' not in second


@pytest.mark.parametrize(
    ('context_toml', 'error'),
    [
        ('[[file]]\npath = "/nonexistent.md"\n', 'not found: /nonexistent.md'),
        ('bogus = 1\n', 'unknown key bogus'),
        ('[[file]]\npath = "${BAKERY_UNSET_VAR}/x.md"\n', 'is not set'),
    ],
)
def test_config_errors_are_reported(
    tmp_path: pathlib.Path,
    run_pi: Callable[[pathlib.Path], harness.Pi],
    context_toml: str,
    error: str,
) -> None:
    pi = run_pi(make_profile(tmp_path, context_toml))

    assert error in pi.run('hi').stderr
