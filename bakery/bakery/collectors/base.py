"""What a collector returns, and what every collector shares."""

import dataclasses

# The Keychain item collectors read GitHub with: the gateway's, never handed
# to a claw. Scout's model only gets a scopeless token, but GitHub refuses
# Actions log downloads to those, even on public repos.
GITHUB_TOKEN = 'BAKERY_GITHUB'


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
