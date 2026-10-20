"""
The claws' own checks on their task list changes, beyond their rules: task
guards the gateway's task server applies to every claw's writes, whichever
command made them.

- Filing: a task is created only if its link is new (see `filing`).
- Asking: every new question, on any change, has its `**Context:**` and its
  `**Default:**`; so everything waiting on the operator is answered the same
  way.
- Proposing: every task baker files asks at least one question, since its
  fixes change the claws themselves and triage routes a task with none
  straight away.
"""

from task import api

from . import filing
from . import store

QUESTION = '--question "<question>\\n**Context:** ...\\n**Default:** ..."'
# Work keys are `<claw>/...`.
BAKER = 'baker/'


def check(change: api.Change) -> None:
    before, after = change.before, change.after
    if after is None or after.namespace != store.NAMESPACE:
        return
    if before is None and after.link:
        filing.check(after.link)
    if before is None and change.actor.name.startswith(BAKER):
        _check_proposal(after)
    _check_questions(before, after)


def _check_proposal(task: api.Task) -> None:
    if not task.conversation.questions:
        raise api.TaskError(
            'a task baker files asks the operator at least one question, eg.'
            ' whether to accept it as is:'
            f' {QUESTION}'
        )


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
