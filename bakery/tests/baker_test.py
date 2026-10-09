import datetime
import json
import pathlib
import subprocess
import time
from collections.abc import Iterable
from collections.abc import Mapping

import pytest

from bakery import state
from bakery.collectors import baker
from bakery.gateway import config
from bakery.gateway import ledger
from testing import gateway as testing_gateway

REVIEW = """
[[job]]
name = "review"
cron = "0 4 * * *"
prompt = "Review."
collector = "baker"
"""
# The previous review started an hour ago.
SINCE = time.time() - 3600


def iso(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).isoformat()


def local(ts: float) -> str:
    return datetime.datetime.fromtimestamp(ts).strftime('%Y-%m-%d %H:%M:%S')


def write_session(
    claw_dir: pathlib.Path, name: str, calls: Iterable[Mapping[str, object]]
) -> None:
    """A transcript: each call is a tool call and its result."""
    entries: list[dict[str, object]] = [
        {'type': 'session', 'id': name, 'timestamp': iso(SINCE - 120)},
        {'type': 'session_info', 'name': name, 'timestamp': iso(SINCE - 120)},
    ]
    for i, call in enumerate(calls):
        at = iso(float(str(call['at'])))
        call_id = f'call-{i}'
        assistant = {
            'role': 'assistant',
            'content': [
                {
                    'type': 'toolCall',
                    'id': call_id,
                    'name': call['tool'],
                    'arguments': call['args'],
                }
            ],
        }
        result = {
            'role': 'toolResult',
            'toolCallId': call_id,
            'toolName': call['tool'],
            'isError': call.get('error', False),
            'content': [{'type': 'text', 'text': call['text']}],
            'details': call.get('details'),
        }
        entries.extend(
            {'type': 'message', 'timestamp': at, 'message': message}
            for message in (assistant, result)
        )
    sessions = claw_dir / 'sessions'
    sessions.mkdir(parents=True, exist_ok=True)
    path = sessions / f'2026-10-09T00-00-00-000Z_{name}.jsonl'
    path.write_text('\n'.join(json.dumps(e) for e in entries) + '\n')


def add_run(
    runs: ledger.Ledger,
    claw: str,
    job: str,
    started: float,
    status: str = 'settled',
    reason: str | None = None,
) -> None:
    runs.db.execute(
        'INSERT INTO runs (claw, work_key, trigger, started_at, status,'
        ' reason, cost_usd, turns, job) VALUES (?, ?, ?, ?, ?, ?, 0.5, 3, ?)',
        (claw, f'{claw}/{job}-x', f'cron:{job}', started, status, reason, job),
    )


@pytest.fixture(name='world', scope='function')
def fixture_world(
    root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> config.Claw:
    monkeypatch.setenv('TASK_FOLDER', str(root / 'tasks'))
    (root / 'tasks').mkdir()
    claws = testing_gateway.make_claws(root, {'baker': REVIEW, 'scout': ''})
    base = state.root()
    scout = base / 'scout'
    (scout / 'memory').mkdir(parents=True)
    (scout / 'MEMORY.md').write_text('# scout\n')
    monkeypatch.setenv('GIT_COMMITTER_DATE', f'@{SINCE - 600:.0f} +0000')
    state.init()
    monkeypatch.delenv('GIT_COMMITTER_DATE')
    (scout / 'MEMORY.md').write_text('# scout\n\n- CI logs need auth\n')
    state.commit('scout/dream-x: settled')
    gateway = base / '_gateway'
    gateway.mkdir()
    (gateway / 'gateway.log').write_text(
        f'{local(SINCE - 60)},1 ERROR bakery.gateway.core: old trouble\n'
        f'{local(SINCE + 60)},1 INFO bakery.gateway.jobs: fine\n'
        f'{local(SINCE + 60)},2 ERROR bakery.gateway.core: committing state'
        ' after triage/dream failed\n'
    )
    return config.load(claws).claw('baker')


def test_digest_covers_activity_since_the_last_review(
    world: config.Claw,
) -> None:
    runs = ledger.Ledger(ledger.default_path())
    add_run(runs, 'scout', 'daily', SINCE - 600, 'failed', 'too old')
    add_run(runs, 'baker', 'review', SINCE)
    add_run(runs, 'scout', 'daily', SINCE + 60, 'parked', 'cost limit')
    scout = state.root() / 'scout'
    gh = 'gh run view 1 --log-failed'
    write_session(
        scout,
        'scout-daily-x',
        [
            {
                'at': SINCE - 60,
                'tool': 'bash',
                'args': {'command': 'echo before'},
                'text': 'cat: x: Operation not permitted',
            },
            *(
                {
                    'at': SINCE + 60 + i,
                    'tool': 'bash',
                    'args': {'command': gh},
                    'error': True,
                    'text': 'gh: open ~/.config/gh/config.yml: operation'
                    ' not permitted\n\nCommand exited with code 1',
                }
                for i in range(2)
            ),
            {
                'at': SINCE + 70,
                'tool': 'bash',
                'args': {'command': 'curl https://github.com'},
                'error': True,
                'text': 'curl: (7) CONNECT tunnel failed, response 403',
                'details': {
                    'sandboxViolations': [
                        'deny network-outbound github.com:443 (host is not'
                        ' on the allow list)'
                    ]
                },
            },
            {
                'at': SINCE + 80,
                'tool': 'read',
                'args': {'path': '/x/secret'},
                'error': True,
                'text': '/x/secret is protected and cannot be read',
            },
            {
                'at': SINCE + 90,
                'tool': 'github',
                'args': {'endpoint': 'repos/a/b/actions/jobs/1/logs'},
                'error': True,
                'text': 'gh api failed (1): terminal escape sequences',
            },
            {
                'at': SINCE + 100,
                'tool': 'memory_append',
                'args': {'text': 'CI logs need auth', 'source': 'self'},
                'text': 'Saved.',
            },
        ],
    )
    subprocess.run(
        ('task', 'add', '--tag', 'bakery/neverfix', '--link',
         'baker:plumbing/x', '--', 'Never this'),
        check=True,
    )  # fmt: skip

    text = baker.collect(world).text

    assert text is not None
    assert f'since="{iso(SINCE)[:19]}' in text
    assert '- scout daily: 1 parked · $0.50 · 3.0 turns/run' in text
    assert 'scout/daily-x parked: cost limit' in text
    assert 'too old' not in text
    assert '- scout (1 sessions): bash 3 (3 failed), read 1 (1 failed)' in text
    assert (
        'scout · sandbox · gh: open ~/.config/gh/config.yml: operation not'
        ' permitted — 2 calls in 1 units; latest scout-daily-x' in text
    )
    assert f'bash `{gh}`' in text
    assert 'scout · sandbox · deny network-outbound github.com:443' in text
    assert 'scout · policy · /x/secret is protected and cannot be read' in text
    assert 'scout · github · gh api failed (1)' in text
    assert 'echo before' not in text
    assert (
        'scout: MEMORY.md 29 chars (1 file changed, 2 insertions(+) in this'
        ' window)' in text
    )
    assert 'appended: 1 self' in text
    assert 'scout/dream-x: settled' in text
    assert 'initialize claw state' not in text
    assert '[bakery/neverfix] None/None Never this (baker:plumbing/x)' in text
    assert 'ERROR bakery.gateway.core: committing state after' in text
    assert 'old trouble' not in text

    # Only baker itself ran since its last review: nothing to review.
    add_run(runs, 'baker', 'review', SINCE + 120)
    add_run(runs, 'baker', 'dream', SINCE + 130)
    assert baker.collect(world).text is None


def test_sections_say_how_to_read_what_they_cut() -> None:
    lines = [f'line {i}' for i in range(10)]

    rendered = baker.Section('T', lines, 20, 'look').render()

    assert rendered == '## T\n\nline 0\nline 1\n[8 more lines cut; look]'
