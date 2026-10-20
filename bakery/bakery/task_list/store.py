"""
The shared task list: the claws' namespace in it, and reading it.

Read through the task library bakery pins, never the `task` on $PATH (the
operator's own install), so its shape is the one this code was written for.
"""

from task import db
from task import errors
from task import query
from task import schema

# The claws' namespace of the task list: the only one their rules let them
# write, so every other task is the operator's.
NAMESPACE = 'bakery'
TaskError = errors.TaskError


def ref(task: dict[str, object]) -> str:
    """A listed task's address, `<namespace>:<id>`."""
    return f'{task["namespace"]}:{task["id"]}'


def notes(task: dict[str, object]) -> str:
    """A listed task's notes: its description, then its comments, in order."""
    comments = task['comments']
    assert isinstance(comments, list)
    said = [task['description'], *(c['body'] for c in comments)]
    return '\n\n'.join(str(part) for part in said if part)


def listed(
    filter_: str = '', order: query.SortOrder = query.SortOrder.tag
) -> list[dict[str, object]]:
    """Every task matching `filter_` (`task list -f` syntax), as JSON."""
    clauses = query.compile_(filter_, -1, -1, order)
    with db.connect() as conn:
        return [task.to_json() for task in db.select(conn, *clauses)]


def shown(address: str) -> dict[str, object]:
    """
    One task, by `[namespace:]id` (a bare id is in `default`); TaskError if
    it cannot be shown.
    """
    with db.connect() as conn:
        return db.get(conn, schema.Ref.parse(address)).to_json()
