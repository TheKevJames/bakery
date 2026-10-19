"""
Filing: a task is added only if no open task, and no rejection, has its
link's lineage.

`check` refuses any claw's task which breaks that (see `hooks`); collectors,
which weigh many candidates at once, use `tracked`.
"""

from . import lineage
from . import store
from . import wontfix

Task = dict[str, object]


def tracked() -> set[str]:
    """Lineages not to file again: open tasks' and rejections'."""
    return {
        lineage.of(str(task['link']))
        for task in store.listed()
        if task.get('link')
    } | set(wontfix.load())


def holder(link: str) -> Task | wontfix.Rejection | None:
    """
    What has `link`'s lineage: an open task (in any namespace), else a
    rejection; None if `link` is new.
    """
    key = lineage.of(link)
    # `link~` is a substring match, so it also finds eg. `…/issues/12` for
    # `…/issues/1`.
    for task in store.listed(f'link~{lineage.source(link)}'):
        if lineage.of(str(task['link'])) == key:
            return task
    return wontfix.load().get(key)


def check(link: str) -> None:
    """Refuse (TaskError) to file a task with `link`, if it is tracked."""
    # `task` splits filters on commas, so its lineage could not be found.
    if ',' in link:
        raise store.TaskError(
            f'cannot file {link}: a link cannot hold a comma'
        )
    held = holder(link)
    if isinstance(held, wontfix.Rejection):
        raise store.TaskError(
            f"not filed: the operator rejected this link's lineage on"
            f' {held.date}, so it is never filed again: {held.reason}'
        )
    if held is not None:
        raise store.TaskError(
            f'not filed: open task {store.ref(held)} already has this'
            f' link\'s lineage ("{held["summary"]}")'
        )
