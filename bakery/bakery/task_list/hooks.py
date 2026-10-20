"""
The claws' own checks on their task list changes, beyond their rules: task
guards the gateway's task server applies to every claw's writes, whichever
command made them.

- Filing: a task is created only if its link is new (see `filing`).
- Asking: a task enters `human`, created there or moved there, only asking
  the operator a new question with `--question`, each with its `**Context:**`
  and its `**Default:**`; so everything waiting on the operator is answered
  the same way.
"""

from task import errors
from task import ops
from task import schema

from . import filing
from . import store

HUMAN = 'human'
QUESTION = '--question "<question>\\n**Context:** ...\\n**Default:** ..."'


def check(change: ops.Change) -> None:
    before, after = change.before, change.after
    if after is None or after.namespace != store.NAMESPACE:
        return
    if before is None and after.link:
        filing.check(after.link)
    if after.tag == HUMAN and (before is None or before.tag != HUMAN):
        _check_questions(before, after)


def _check_questions(before: schema.Task | None, after: schema.Task) -> None:
    """What the change asked must ask the operator something answerable."""
    asked = len(before.conversation.questions) if before else 0
    questions = after.conversation.questions[asked:]
    if not questions:
        raise errors.TaskError(
            f'a task enters {HUMAN} only asking the operator a new question:'
            f' add {QUESTION} to the `set --tag {HUMAN}` or `add` which moves'
            ' it there'
        )
    if not all(
        '**Context:**' in q.body and '**Default:**' in q.body
        for q in questions
    ):
        raise errors.TaskError(
            'each question needs its **Context:** and its **Default:**:'
            f' {QUESTION}'
        )
