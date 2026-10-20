"""
Claws as agents of the task list.

A claw's unit of work is the agent: its work key (eg. `triage/task-151`) is
who claims, and the claw's rules (in gateway.toml) plus `hooks` are what it
may do. A claim never outlives the run holding it: the gateway releases a
unit's claims whenever its run ends, however it ends, and every claw's when
the gateway starts, in case it stopped without doing so.
"""

from task import ops
from task import rules
from task import schema

from . import hooks
from . import store


def writer(claw_rules: rules.Rules, work_key: str) -> ops.Writer:
    """Writes as the unit `work_key`, under its claw's rules."""

    def guard(change: ops.Change) -> None:
        claw_rules.check(change)
        hooks.check(change)

    return ops.Writer(schema.Actor.agent(work_key), guard)


def claim(
    claw_rules: rules.Rules, work_key: str, address: str
) -> dict[str, object] | None:
    """Task `address`, now held by the unit `work_key`; None if it cannot."""
    try:
        task = writer(claw_rules, work_key).claim(schema.Ref.parse(address))
    except schema.TaskError:
        return None
    return task.to_json()


def release(work_key: str) -> list[str]:
    """Release everything the unit `work_key` claims; the addresses."""
    return _release(
        store.listed(f'namespace={store.NAMESPACE},claim={work_key}')
    )


def sweep(claw: str) -> list[str]:
    """Release everything any of `claw`'s units claims; the addresses."""
    # Filters fold case and `~` is a substring match, so filter exactly here.
    listed = store.listed(f'namespace={store.NAMESPACE},claim~{claw}/')
    return _release(
        [t for t in listed if str(t['claim']).startswith(f'{claw}/')]
    )


def _release(tasks: list[dict[str, object]]) -> list[str]:
    for task in tasks:
        agent = ops.Writer(schema.Actor.agent(str(task['claim'])))
        agent.release(schema.Ref.parse(store.ref(task)))
    return [store.ref(task) for task in tasks]
