"""The shared task list, as the claws find it."""

import json
import os
import pathlib
import subprocess

from bakery.task_list import store


def create(folder: pathlib.Path) -> None:
    """A task list in `folder`, with the claws' namespace."""
    folder.mkdir()
    subprocess.run(
        ('task', 'namespace', 'add', store.NAMESPACE),
        check=True,
        env=os.environ | {'TASK_FOLDER': str(folder)},
    )


def task(*args: str) -> str:
    """Run the task CLI on $TASK_FOLDER; its stdout."""
    return subprocess.run(
        ('task', *args), capture_output=True, text=True, check=True
    ).stdout


def show(address: str) -> dict[str, object]:
    shown: dict[str, object] = json.loads(task('show', address, '--json'))
    return shown


def said(address: str) -> list[str]:
    """What a task's comments say, in order."""
    comments = show(address)['comments']
    assert isinstance(comments, list)
    return [str(comment['body']) for comment in comments]
