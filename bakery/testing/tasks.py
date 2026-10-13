"""The shared task list, as the claws find it."""

import os
import pathlib
import subprocess

from bakery.collectors import tasks


def create(folder: pathlib.Path) -> None:
    """A task list in `folder`, with the claws' namespace."""
    folder.mkdir()
    subprocess.run(
        ('task', 'namespace', 'add', tasks.NAMESPACE),
        check=True,
        env=os.environ | {'TASK_FOLDER': str(folder)},
    )
