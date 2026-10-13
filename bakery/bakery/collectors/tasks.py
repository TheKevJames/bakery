"""The shared task list: the claws' namespace in it, and reading it."""

import json
import re
import subprocess

TAG_RE = re.compile(r'[a-z0-9][a-z0-9_.-]*')
# The claws' namespace of the task list: the sandbox lets them write only its
# directory, so every other task is the operator's.
NAMESPACE = 'bakery'


def ref(task: dict[str, object]) -> str:
    """A listed task's address, `<namespace>:<id>`."""
    return f'{task["namespace"]}:{task["id"]}'


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


def shown(address: str) -> dict[str, object]:
    """
    One task, by `[namespace:]id` (a bare id is in `default`); TaskError, with
    `task`'s message, if it cannot be shown.
    """
    result = subprocess.run(
        ('task', 'show', address, '--json'),
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
