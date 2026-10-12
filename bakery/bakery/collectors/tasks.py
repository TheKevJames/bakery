"""The shared task list, as collectors read it."""

import json
import subprocess


class TaskError(Exception):
    pass


def listed(*args: str) -> list[dict[str, object]]:
    out = subprocess.run(
        ('task', 'list', '--json', *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    tasks: list[dict[str, object]] = json.loads(out)
    return tasks


def shown(ident: int) -> dict[str, object]:
    """One task; TaskError, with `task`'s message, if it cannot be shown."""
    result = subprocess.run(
        ('task', 'show', str(ident), '--json'),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        raise TaskError(result.stderr.strip())
    task: dict[str, object] = json.loads(result.stdout)
    return task


def lineage(link: str) -> str:
    """A link without its fragment, since line anchors move."""
    return link.split('#', 1)[0]
