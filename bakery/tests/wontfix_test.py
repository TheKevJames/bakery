import datetime
import json
import pathlib
import subprocess

import pytest

from bakery import state
from bakery.task_list import wontfix
from testing import commands
from testing import tasks as testing_tasks


def bakery(*args: str) -> subprocess.CompletedProcess[str]:
    return commands.bakery('wontfix', *args)


@pytest.fixture(name='tasks', scope='function')
def fixture_tasks(
    tmp_path: pathlib.Path,
    task_folder: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    testing_tasks.create(task_folder)
    for args in (
        ('-n', 'bakery', '--tag', 'wontfix', '--link', 'https://x/a?todo=f#L3',
         'a'),
        ('-n', 'bakery', '--tag', 'wontfix', 'no link'),
        ('--link', 'https://x/B', 'mine'),
    ):  # fmt: skip
        testing_tasks.task('add', *args)


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
        'https://x/B': {'date': today, 'reason': 'mine', 'summary': 'mine'},
    }
    # Rejecting keeps the task: the operator closes it themself.
    assert len(json.loads(testing_tasks.task('list', '--json'))) == 3
    assert 'https://x/B\n    mine\n    mine\n' in bakery('list').stdout

    # Any link with the lineage unrejects it.
    assert bakery('rm', 'https://x/b#L1').returncode == 0
    assert 'https://x/B is not rejected' in bakery('rm', 'https://x/B').stderr
    assert set(wontfix.load()) == {'https://x/a?todo=f'}
    log = state.git('log', '--format=%s').stdout.splitlines()
    assert log[:3] == [
        'wontfix: rm https://x/B',
        'wontfix: add https://x/B',
        'wontfix: add https://x/a?todo=f',
    ]
