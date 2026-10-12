import datetime
import json
import pathlib
import subprocess
import sys

import pytest

from bakery import state
from bakery.collectors import wontfix


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, check=False)


def bakery(*args: str) -> subprocess.CompletedProcess[str]:
    return run(sys.executable, '-m', 'bakery.cli', 'wontfix', *args)


@pytest.fixture(name='tasks', scope='function')
def fixture_tasks(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    monkeypatch.setenv('TASK_FOLDER', str(tmp_path / 'tasks'))
    (tmp_path / 'tasks').mkdir()
    for args in (
        ('--tag', 'bakery/wontfix', '--link', 'https://x/a?todo=f#L3', 'a'),
        ('--tag', 'bakery/wontfix', 'no link'),
        ('--link', 'https://x/b', 'mine'),
    ):
        run('task', 'add', *args).check_returncode()


def test_rejections_are_recorded_by_lineage_and_committed(tasks: None) -> None:
    del tasks
    added = bakery('add', '1', '--reason', 'prose, not a todo')
    assert added.stdout == 'rejected https://x/a?todo=f\n'
    assert bakery('add', '3', '--reason', 'mine').returncode == 0
    for args, error in (
        (('1', '--reason', 'again'), 'https://x/a?todo=f is already rejected'),
        (('2', '--reason', 'x'), 'task 2 has no link'),
        (('3', '--reason', ' '), 'the reason is empty'),
        (('99', '--reason', 'x'), 'cannot reject #99: '),
    ):
        refused = bakery('add', *args)
        assert refused.returncode and error in refused.stderr, args

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    assert json.loads(wontfix.path().read_text()) == {
        'https://x/a?todo=f': {
            'date': today,
            'reason': 'prose, not a todo',
            'summary': 'a',
        },
        'https://x/b': {'date': today, 'reason': 'mine', 'summary': 'mine'},
    }
    # Rejecting keeps the task: Kevin closes it himself.
    assert len(json.loads(run('task', 'list', '--json').stdout)) == 3
    assert 'https://x/b\n    mine\n    mine\n' in bakery('list').stdout

    assert bakery('rm', 'https://x/b').returncode == 0
    assert 'https://x/b is not rejected' in bakery('rm', 'https://x/b').stderr
    assert set(wontfix.load()) == {'https://x/a?todo=f'}
    log = state.git('log', '--format=%s').stdout.splitlines()
    assert log[:3] == [
        'wontfix: rm https://x/b',
        'wontfix: add https://x/b',
        'wontfix: add https://x/a?todo=f',
    ]
