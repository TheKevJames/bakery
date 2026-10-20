"""What a collector returns, and what every collector shares."""

import dataclasses
from typing import TYPE_CHECKING

from task import api

if TYPE_CHECKING:
    from ..gateway import config

# The Keychain item collectors read GitHub with: the gateway's, never handed
# to a claw. Scout's model only gets a scopeless token, but GitHub refuses
# Actions log downloads to those, even on public repos.
GITHUB_TOKEN = 'BAKERY_GITHUB'


def task_unit(claw: 'config.Claw', task: api.Task) -> str:
    """
    A task's own unit of work for `claw` (eg. `triage/task-151`), so its
    thread and session last as long as the task.
    """
    return f'{claw.name}/task-{task.ident}'


@dataclasses.dataclass(frozen=True)
class Collection:
    # Appended to the job's prompt; None when there is nothing to do.
    text: str | None
    # Problems worth telling the operator about (eg. a repo that could not be
    # read).
    errors: tuple[str, ...] = ()
    # The unit of work to run, if not a new one per run (eg. one per task, so
    # a task keeps its thread and session).
    work_key: str | None = None
