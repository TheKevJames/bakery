import datetime
import json
import pathlib
import subprocess
import sys

import pytest

from bakery import state
from bakery.collectors import wontfix
from testing import tasks as testing_tasks


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
    testing_tasks.create(tmp_path / 'tasks')
    for args in (
        ('-n', 'bakery', '--tag', 'wontfix', '--link', 'https://x/a?todo=f#L3',
         'a'),
        ('-n', 'bakery', '--tag', 'wontfix', 'no link'),
        ('--link', 'https://x/b', 'mine'),
    ):  # fmt: skip
        run('task', 'add', *args).check_returncode()


def test_rejections_are_recorded_by_lineage_and_committed(tasks: None) -> None:
    del tasks
    added = bakery('add', 'bakery:1', '--reason', 'prose, not a todo')
    assert added.stdout == 'rejected https://x/a?todo=f\n'
    assert bakery('add', '1', '--reason', 'mine').returncode == 0
    for args, error in (
        (
            ('bakery:1', '--reason', 'again'),
            'https://x/a?todo=f is already rejected',
        ),
        (('bakery:2', '--reason', 'x'), 'task bakery:2 has no link'),
        (('1', '--reason', ' '), 'the reason is empty'),
        (
            ('99', '--reason', 'x'),
            'cannot reject 99: error: task 99 not found',
        ),
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
    # Rejecting keeps the task: the operator closes it themself.
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
