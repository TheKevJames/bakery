import hypothesis
from hypothesis import strategies as st

from bakery.collectors import scout_todos

NOTES = st.from_regex(r'[A-Za-z0-9 ,.]{0,40}', fullmatch=True)


def found(path: str, source: str) -> list[tuple[int, str]]:
    return [(t.lineno, t.text) for t in scout_todos.todos(path, source)]


@hypothesis.given(note=NOTES, blank_lines=st.integers(0, 3))
def test_keeps_comments_but_not_strings(note: str, blank_lines: int) -> None:
    lines = [
        *[''] * blank_lines,
        f'"""TODO: {note}"""',
        f"x = 'FIXME: {note}'",
        f"y = 'TODO: {note}'  # FIXME: {note}",
        f'# TODO: {note}',
    ]

    todos = found('app.py', '\n'.join(lines) + '\n')

    assert todos == [
        (n, lines[n - 1]) for n in (blank_lines + 3, blank_lines + 4)
    ]


def test_drops_prose() -> None:
    source = '# Notes\n\nTODO: write these, and `FIXME` too.\n'

    assert not found('README.md', source)
    assert not found('README.rst', source)


def test_keeps_every_mention_without_a_lexer() -> None:
    source = 'gron = gron  ; TODO: verify\n"FIXME"\nTODOs\nMY_TODO\n'

    assert found('packages', source) == [
        (1, 'gron = gron  ; TODO: verify'),
        (2, '"FIXME"'),
    ]
