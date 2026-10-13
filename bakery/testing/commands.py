"""Command-line tools the tests drive: git, and bakery's own CLI."""

import pathlib
import subprocess
import sys


def git(path: pathlib.Path, *args: str) -> str:
    """Run git in `path`; its stdout."""
    return subprocess.run(
        ('git', '-C', str(path), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def bakery(*args: str) -> subprocess.CompletedProcess[str]:
    """Run the `bakery` CLI, as the operator would."""
    return subprocess.run(
        (sys.executable, '-m', 'bakery.cli', *args),
        capture_output=True,
        text=True,
        check=False,
    )
