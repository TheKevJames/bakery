"""
The claws' own checks on their task list changes, beyond their rules: task
guards the gateway's task server applies to every claw's writes, whichever
command made them.

- Filing: a task is created only if its link is new (see `filing`).
- Asking: every new question, on any change, has its `**Context:**` and its
  `**Default:**`; so everything waiting on the operator is answered the same
  way.
"""

from task import api

from . import filing
from . import store

QUESTION = '--question "<question>\\n**Context:** ...\\n**Default:** ..."'


def check(change: api.Change) -> None:
    before, after = change.before, change.after
    if after is None or after.namespace != store.NAMESPACE:
        return
    if before is None and after.link:
        filing.check(after.link)
    _check_questions(before, after)


def _check_questions(before: api.Task | None, after: api.Task) -> None:
    """What the change asked must be answerable cold."""
    asked = len(before.conversation.questions) if before else 0
    questions = after.conversation.questions[asked:]
    if not all(
        '**Context:**' in q.body and '**Default:**' in q.body
        for q in questions
    ):
        raise api.TaskError(
            'each question needs its **Context:** and its **Default:**:'
            f' {QUESTION}'
        )
