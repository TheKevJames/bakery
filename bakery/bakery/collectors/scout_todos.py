"""
Scout's TODOs: TODO/FIXME comments in a repo's tree.

`git grep` finds the files that mention either word, then Pygments, picking
a lexer by file name, keeps only the mentions inside comments: strings,
docstrings and prose (eg. test fixtures, or docs about TODOs) are not tasks.
Files Pygments has no lexer for keep every mention.
"""

import dataclasses
import re
import subprocess
from collections.abc import Iterator

from pygments import lexers
from pygments import token
from pygments import util

from . import scout_repos

PATTERN = 'TODO|FIXME'
# The words `git grep -w` matches: ASCII letters, digits and underscores.
MENTION_RE = re.compile(rf'(?<!\w)(?:{PATTERN})(?!\w)', re.ASCII)
CONTEXT_LINES = 3


@dataclasses.dataclass(frozen=True)
class Todo:
    path: str
    lineno: int
    text: str
    # The lines around it, its own included.
    context: tuple[str, ...]


def _comment_lines(path: str, source: str) -> set[int] | None:
    """Lines with a mention inside a comment; None if no lexer fits."""
    try:
        # Stripping leading newlines would shift every line number.
        lexer = lexers.get_lexer_for_filename(path, source, stripnl=False)
    except util.ClassNotFound:
        return None
    found: set[int] = set()
    lineno = 1
    for kind, value in lexer.get_tokens(source):
        if kind in token.Comment:
            found.update(
                lineno + value.count('\n', 0, mention.start())
                for mention in MENTION_RE.finditer(value)
            )
        lineno += value.count('\n')
    return found


def todos(path: str, source: str) -> Iterator[Todo]:
    """The TODOs in a file's source."""
    comments = _comment_lines(path, source)
    lines = source.removesuffix('\n').split('\n')
    for lineno, text in enumerate(lines, 1):
        if not MENTION_RE.search(text):
            continue
        if comments is not None and lineno not in comments:
            continue
        context = lines[
            max(0, lineno - 1 - CONTEXT_LINES) : lineno + CONTEXT_LINES
        ]
        yield Todo(path, lineno, text, tuple(context))


def find(repo: scout_repos.Repo, ref: str) -> Iterator[Todo]:
    """The TODOs at `ref`, outside the repo's excluded paths."""
    pathspecs = ['.', *(f':(exclude,glob){p}' for p in repo.exclude)]
    try:
        out = scout_repos.git(
            repo, 'grep', '-l', '-z', '-I', '-w', '-E', PATTERN, ref, '--',
            *pathspecs,
        )  # fmt: skip
    except subprocess.CalledProcessError as e:
        if e.returncode == 1:  # no matches
            return
        raise
    for match in filter(None, out.split('\0')):
        path = match.removeprefix(f'{ref}:')
        yield from todos(path, scout_repos.git(repo, 'show', match))
