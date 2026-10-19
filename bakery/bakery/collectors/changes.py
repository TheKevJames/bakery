"""
The change feed: commits to the bakery checkout the claws run from (its
HEAD, including the operator's unmerged work), since the claw's last settled
dream, so the dream can recheck what those changes may have made wrong in its
memory (eg. a widened sandbox, a removed tool, a new scout exclude).

Each run records the HEAD its input was gathered against (the ledger's
`bakery_commit`), and the next dream starts from there. Without one (a
claw's first dream, or a commit since rebased away and collected), it falls
back to FALLBACK, the memory rules' expiry window. Bots' commits (renovate's
dependency bumps) are left out.
"""

import datetime
import pathlib
import subprocess
from typing import TYPE_CHECKING

from .. import paths
from ..gateway import ledger
from . import base

if TYPE_CHECKING:
    from ..gateway import config

FALLBACK = datetime.timedelta(days=14)
FIELD = '\x1f'
RECORD = '\x1e'


def _git(repo: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ('git', '-C', str(repo), *args),
        capture_output=True,
        check=True,
        text=True,
    ).stdout


def head(repo: pathlib.Path) -> str:
    """The commit `repo` has checked out."""
    return _git(repo, 'rev-parse', 'HEAD').strip()


def _exists(repo: pathlib.Path, commit: str) -> bool:
    try:
        _git(repo, 'cat-file', '-e', f'{commit}^{{commit}}')
    except subprocess.CalledProcessError:
        return False
    return True


def _job(claw: 'config.Claw') -> str:
    return next(j.name for j in claw.jobs if j.collector == 'changes')


def _commits(repo: pathlib.Path, since: str | None) -> list[str]:
    """Each non-bot commit after `since` (else in FALLBACK), oldest first."""
    if since is None:
        cutoff = datetime.datetime.now(datetime.UTC) - FALLBACK
        revisions = [f'--since={cutoff.isoformat()}', 'HEAD']
    else:
        revisions = [f'{since}..HEAD']
    out = _git(
        repo, 'log', '--reverse', '--no-merges', '--name-only',
        f'--format={RECORD}%h{FIELD}%an{FIELD}%cs{FIELD}%B{FIELD}',
        *revisions, '--',
    )  # fmt: skip
    found = []
    for record in out.split(RECORD)[1:]:
        sha, author, date, message, names = record.split(FIELD)
        if author.endswith('[bot]'):
            continue
        files = ', '.join(names.split())
        found.append(
            f'<commit sha="{sha}" date="{date}">\n{message.strip()}\n\n'
            f'Files: {files}\n</commit>'
        )
    return found


def collect(claw: 'config.Claw', work_key: str) -> base.Collection:
    del work_key
    repo = paths.repo()
    runs_db = ledger.Ledger(ledger.default_path())
    try:
        since = runs_db.last_settled_commit(claw.name, _job(claw))
    finally:
        runs_db.close()
    if since is not None and not _exists(repo, since):
        since = None
    commits = _commits(repo, since)
    if not commits:
        return base.Collection(None)
    window = (
        f'since {since[:12]}, the HEAD your last dream covered'
        if since
        else f'in the last {FALLBACK.days} days (no earlier dream on record)'
    )
    return base.Collection(
        f'<changes checkout="{repo}">\n'
        f"The operator's commits to the bakery checkout you run from, {window}"
        ':\n\n' + '\n\n'.join(commits) + '\n</changes>'
    )
