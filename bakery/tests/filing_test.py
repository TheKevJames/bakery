import datetime
import json
import pathlib
import subprocess

import hypothesis
import pytest
from hypothesis import strategies as st

from bakery.task_list import lineage
from bakery.task_list import wontfix
from testing import commands
from testing import tasks as testing_tasks

REPO = 'https://github.com/Owner/repo'
# Links are URLs: ASCII, with `#` only before their anchor.
SOURCES = st.text(
    st.characters(
        min_codepoint=0x21, max_codepoint=0x7E, exclude_characters='#'
    ),
    min_size=1,
)


@hypothesis.given(source=SOURCES, anchor=st.text(), other=SOURCES)
def test_lineage_ignores_only_anchors_and_case(
    source: str, anchor: str, other: str
) -> None:
    key = lineage.of(source)
    assert lineage.of(f'{source}#{anchor}') == key
    assert lineage.of(source.swapcase()) == key
    assert lineage.of(key) == key
    assert (lineage.of(other) == key) == (other.lower() == source.lower())


def file(*args: str) -> subprocess.CompletedProcess[str]:
    return commands.bakery('task', 'add', *args)


def filed(*args: str) -> dict[str, object]:
    result = file('--json', *args)
    print(result.stderr)
    outcome: dict[str, object] = json.loads(result.stdout)
    return outcome


@pytest.fixture(name='tasks', scope='function')
def fixture_tasks(
    tmp_path: pathlib.Path,
    task_folder: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path / 'state'))
    testing_tasks.create(task_folder)
    for args in (
        ('--link', f'{REPO}/blob/main/a.py?todo=f#L3', 'theirs'),
        ('-n', 'bakery', '--tag', 'wontfix', '--link', f'{REPO}/issues/5',
         'not ours'),
    ):  # fmt: skip
        testing_tasks.task('add', *args)
    wontfix.add('bakery:1', 'upstream')
    testing_tasks.task('bakery:1', 'done')


def test_tasks_are_filed_unless_their_lineage_is_tracked(tasks: None) -> None:
    del tasks
    claws = ('-n', 'bakery', '--tag', 'triage')
    twelve = filed(*claws, '--link', f'{REPO}/issues/12', '--', '-twelve')
    assert twelve['filed'] == testing_tasks.show('bakery:2')
    # `…/issues/12` holds `…/issues/1`, but is another source.
    one = filed(*claws, '--description', 'd', '--link', f'{REPO}/issues/1',
                '--', 'one')  # fmt: skip
    assert one['filed'] == testing_tasks.show('bakery:3')
    again = filed('--link', f'{REPO.lower()}/issues/1#issuecomment-1', 'x')
    assert again == {'open': testing_tasks.show('bakery:3')}
    # Open in any namespace, whatever the anchor.
    assert file('--link', f'{REPO}/blob/main/a.py?todo=f#L9', 'x').stdout == (
        'open as default:1: theirs\n'
    )

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    assert filed('--link', f'{REPO}/issues/5#top', 'x') == {
        'rejected': {
            'link': f'{REPO}/issues/5',
            'summary': 'not ours',
            'reason': 'upstream',
            'date': today,
        }
    }
    assert file('--link', f'{REPO}/issues/5', 'x').stdout == (
        f'rejected on {today} ({REPO}/issues/5): upstream\n'
    )

    comma = file('--link', f'{REPO}/issues/6,7', 'x')
    assert comma.returncode and 'cannot hold a comma' in comma.stderr
    bad = file('--priority', 'urgent', '--link', f'{REPO}/issues/8', 'x')
    assert bad.returncode and 'urgent' in bad.stderr
    unlinked = file('-n', 'bakery', '--', 'no link')
    assert unlinked.stdout == 'added task bakery:4: no link\n'
    assert filed('no link')['filed'] == testing_tasks.show('default:2')
    assert len(json.loads(testing_tasks.task('list', '--json'))) == 5

    # Only `task add` passes on arguments bakery does not know.
    refused = commands.bakery('wontfix', 'list', '--link', 'x')
    assert refused.returncode and 'unrecognized arguments' in refused.stderr
