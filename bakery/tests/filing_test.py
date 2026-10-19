import datetime
import pathlib

import hypothesis
import pytest
from hypothesis import strategies as st

from bakery.task_list import filing
from bakery.task_list import lineage
from bakery.task_list import store
from bakery.task_list import wontfix
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


def refusal(link: str) -> str | None:
    try:
        filing.check(link)
    except store.TaskError as e:
        return str(e)
    return None


def test_tasks_are_filed_unless_their_lineage_is_tracked(tasks: None) -> None:
    del tasks
    testing_tasks.task(
        'add', '-n', 'bakery', '--link', f'{REPO}/issues/12', 'x'
    )
    # `…/issues/12` holds `…/issues/1`, but is another source.
    assert refusal(f'{REPO}/issues/1') is None
    assert refusal(f'{REPO.lower()}/issues/12#issuecomment-1') == (
        'not filed: open task bakery:2 already has this link\'s lineage ("x")'
    )
    # Open in any namespace, whatever the anchor.
    theirs = refusal(f'{REPO}/blob/main/a.py?todo=f#L9')
    assert theirs is not None and 'open task default:1' in theirs

    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    assert refusal(f'{REPO}/issues/5#top') == (
        "not filed: the operator rejected this link's lineage on"
        f' {today}, so it is never filed again: upstream'
    )
    comma = refusal(f'{REPO}/issues/6,7')
    assert comma is not None and 'cannot hold a comma' in comma
