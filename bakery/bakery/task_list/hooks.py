"""
The claws' own checks on their task list changes, beyond their rules: task
guards the gateway's task server applies to every claw's writes, whichever
command made them.

- Filing: a task is created only if its link is new (see `filing`).
- Blocking: a task enters `blocked` only with a valid `**Blocked on:**` list
  of blockers which have not cleared (see `blocked`).
- Asking: a task enters `human`, created there or moved there, only with a
  new `## Questions` section saying what the operator is asked, as
  pi/claw-extensions/ask-operator.ts writes it; so everything waiting on the
  operator is answered the same way, by number.
"""

import re

from task import ops
from task import schema

from . import blocked
from . import filing
from . import store

HUMAN = 'human'
QUESTIONS_RE = re.compile(
    r'^## Questions \(\d{4}-\d{2}-\d{2}\)$', re.MULTILINE
)
ITEM_RE = re.compile(r'^\d+\. ', re.MULTILINE)


def check(change: ops.Change) -> None:
    before, after = change.before, change.after
    if after is None or after.namespace != store.NAMESPACE:
        return
    if before is None and after.link:
        filing.check(after.link)
    if after.tag == blocked.TAG and (
        before is None or before.tag != after.tag
    ):
        ref = None if after.ident is None else str(after.ref)
        found = blocked.problems(ref, after.description, blocked.Board.load())
        if found:
            raise schema.TaskError(
                f'cannot block {ref or "a new task"}: ' + '; '.join(found)
            )
    if after.tag == HUMAN and (before is None or before.tag != HUMAN):
        earlier = None if before is None else before.description
        _check_questions(earlier, after.description)


def _check_questions(before: str | None, after: str | None) -> None:
    """What the change added must ask the operator something."""
    added = (after or '')[len(before or '') :]
    sections = QUESTIONS_RE.split(added)
    if len(sections) < 2:
        raise schema.TaskError(
            f'a task enters {HUMAN} only with a new `## Questions (<date>)`'
            ' section: use ask_operator, or end a new task with one'
        )
    items = ITEM_RE.split(sections[-1])[1:]
    if not items or not all(
        '**Context:**' in item and '**Default:**' in item for item in items
    ):
        raise schema.TaskError(
            'each question needs a number, its **Context:**, and its'
            ' **Default:**, as ask_operator writes them'
        )
