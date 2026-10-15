"""
Claw state: $XDG_STATE_HOME/claws, a local-only git repo.

Memory and identity files are committed after every change so a poisoned or
botched edit can be reviewed and rolled back; transcripts, clones, each
claw's home and temp dirs, and the gateway's runtime files are ignored.
"""

import importlib.resources
import pathlib
import subprocess

from . import paths

SHARED = '_shared'
GITIGNORE = """\
/_gateway/
/*/sessions/
/*/clones/
/*/home/
/*/tmp/
"""


def root() -> pathlib.Path:
    return paths.resolve_base(
        'XDG_STATE_HOME', pathlib.Path.home() / '.local' / 'state', 'claws'
    )


def operator_file() -> pathlib.Path:
    return root() / SHARED / 'OPERATOR.md'


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ('git', '-C', str(root()), *args),
        check=True,
        capture_output=True,
        text=True,
    )


def commit(message: str, *owned: str) -> bool:
    """
    Commit the changes under the `owned` paths (relative to the state root),
    and only those; False if there were none. Claws' runs overlap, so
    committing anything wider would file one claw's changes under another's
    run.
    """
    if not owned:
        raise ValueError('commit needs at least one path')
    git('add', '--all', '--', *owned)
    if not git('status', '--porcelain', '--', *owned).stdout.strip():
        return False
    # With paths, commit records only them, whatever else is staged.
    git('commit', '--quiet', '--message', message, '--', *owned)
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
    seeded: list[pathlib.Path] = []
    gitignore = base / '.gitignore'
    lines = gitignore.read_text().splitlines() if gitignore.exists() else []
    missing = [line for line in GITIGNORE.splitlines() if line not in lines]
    if missing:
        gitignore.write_text(''.join(f'{line}\n' for line in lines + missing))
        seeded.append(gitignore)
    if not operator_file().exists():
        template = importlib.resources.files('bakery') / 'templates'
        operator_file().write_text((template / 'OPERATOR.md').read_text())
        seeded.append(operator_file())
    if seeded:
        commit(
            'chore: initialize claw state',
            *(str(path.relative_to(base)) for path in seeded),
        )
