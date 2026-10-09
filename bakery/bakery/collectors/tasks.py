"""The shared task list, as collectors read it."""

import json
import subprocess

# Done tasks here are permanent rejections: their links are never filed again.
# Done tasks anywhere else free their link, so a gap which returns is refiled.
REJECTED = 'bakery/wontfix'


def listed(*args: str) -> list[dict[str, object]]:
    out = subprocess.run(
        ('task', 'list', '--json', *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tasks: list[dict[str, object]] = json.loads(out)
    return tasks


def tracked(filter_: str = '') -> list[dict[str, object]]:
    """Tasks whose links must not be filed again: open ones and rejections."""
    rejected = ','.join(f for f in (f'tag={REJECTED}', filter_) if f)
    return listed('-f', filter_) + listed('--done', '-f', rejected)
