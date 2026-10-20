"""
Claws as agents of the task list.

A claw's unit of work is the agent: its work key (eg. `triage/task-151`) is
who claims, and the claw's rules (in gateway.toml) plus `hooks` are what it
may do. A claim never outlives the run holding it: the gateway releases a
unit's claims whenever its run ends, however it ends, and every claw's when
the gateway starts, in case it stopped without doing so.
"""

from collections.abc import Callable

from task import api

from . import hooks
from . import store


def writer(claw_rules: api.Rules, work_key: str) -> api.Writer:
    """Writes as the unit `work_key`, under its claw's rules."""

    def guard(change: api.Change) -> None:
        claw_rules.check(change)
        hooks.check(change)

    return api.Writer(api.Actor.agent(work_key), guard)


def claim(
    claw_rules: api.Rules, work_key: str, address: str
) -> api.Task | None:
    """Task `address`, now held by the unit `work_key`; None if it cannot."""
    try:
        return writer(claw_rules, work_key).claim(api.Ref.parse(address))
    except api.TaskError:
        return None


def claim_first(
    claw_rules: api.Rules,
    tasks: list[api.Task],
    unit: Callable[[api.Task], str],
) -> tuple[int, api.Task] | None:
    """
    The first of `tasks` its own unit (`unit(task)`) can claim, now held by
    it, and its index; None if none can. Someone else may claim a task
    between its listing and its claim.
    """
    for i, listed in enumerate(tasks):
        task = claim(claw_rules, unit(listed), store.ref(listed))
        if task is not None:
            return i, task
    return None


def release(work_key: str) -> list[str]:
    """Release everything the unit `work_key` claims; the addresses."""
    return _release(
        store.listed(f'namespace={store.NAMESPACE},claim={work_key}')
    )


def sweep(claw: str) -> list[str]:
    """Release everything any of `claw`'s units claims; the addresses."""
    return _release(
        store.listed(f'namespace={store.NAMESPACE},claim^={claw}/')
    )


def _release(tasks: list[api.Task]) -> list[str]:
    for task in tasks:
        assert task.claim, 'listed by its claim'
        api.Writer(api.Actor.agent(task.claim)).release(task.ref)
    return [store.ref(task) for task in tasks]
