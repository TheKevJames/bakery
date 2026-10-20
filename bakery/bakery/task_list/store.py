"""
The shared task list: the claws' namespace in it, and reading it.

Read through the task library bakery pins, never the `task` on $PATH (the
operator's own install), so its shape is the one this code was written for.
"""

from task import api

# The claws' namespace of the task list: the only one their rules let them
# write, so every other task is the operator's.
NAMESPACE = 'bakery'
TaskError = api.TaskError


def ref(task: api.Task) -> str:
    """A task's address, always `<namespace>:<id>`."""
    return f'{task.namespace}:{task.ident}'


def listed(
    filter_: str = '', order: api.SortOrder = api.SortOrder.tag
) -> list[api.Task]:
    """
    Every task matching `filter_` (`task list -f` syntax), blocked or not
    unless it says (eg. `blocked=no`).
    """
    return api.list_(f'blocked=any,{filter_}', sort=order)


def shown(address: str) -> api.Task:
    """
    One task, by `[namespace:]id` (a bare id is in `default`); TaskError if
    it cannot be shown.
    """
    return api.get(api.Ref.parse(address))
