"""
Claw state: $XDG_STATE_HOME/claws, a local-only git repo.

Memory and identity files are committed after every change so a poisoned or
botched edit can be reviewed and rolled back; transcripts, worktrees, and the
gateway's runtime files are ignored.
"""

import importlib.resources
import pathlib
import subprocess

from . import paths

SHARED = '_shared'
GITIGNORE = """\
/_gateway/
/*/sessions/
/*/worktrees/
"""


def root() -> pathlib.Path:
    return paths.resolve_base(
        'XDG_STATE_HOME', pathlib.Path.home() / '.local' / 'state', 'claws'
    )


def user_file() -> pathlib.Path:
    return root() / SHARED / 'USER.md'


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ('git', '-C', str(root()), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def commit(message: str) -> bool:
    """Commit every change under the state root; False if there was none."""
    git('add', '--all')
    if not git('status', '--porcelain').stdout.strip():
        return False
    git('commit', '--quiet', '--message', message)
    return True


def init() -> None:
    """Create the state repo and seed shared files; safe to re-run."""
    base = root()
    (base / SHARED).mkdir(parents=True, exist_ok=True)
    if not (base / '.git').is_dir():
        git('init', '--quiet')
        # Commits are made unattended by the gateway, where a signing prompt
        # (eg. gpg pinentry) would hang forever. A fixed identity also marks
        # them as automated in the log.
        git('config', 'commit.gpgsign', 'false')
        git('config', 'user.name', 'bakery')
        git('config', 'user.email', 'bakery@localhost')
    gitignore = base / '.gitignore'
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE)
    if not user_file().exists():
        template = importlib.resources.files('bakery') / 'templates'
        user_file().write_text((template / 'USER.md').read_text())
    commit('chore: initialize claw state')
