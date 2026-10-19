"""
Claws as agents of the task list.

A claw's unit of work is the agent: its work key (eg. `triage/task-151`) is
who claims, and the claw's rules (in gateway.toml) plus `hooks` are what it
may do. A claim never outlives the run holding it: the gateway releases a
unit's claims whenever its run ends, however it ends, and every claw's when
the gateway starts, in case it stopped without doing so.
"""

from collections.abc import Callable

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


def claim(claw_rules: rules.Rules, work_key: str, address: str) -> bool:
    """Whether the unit `work_key` now holds task `address`."""
    try:
        writer(claw_rules, work_key).claim(schema.Ref.parse(address))
    except schema.TaskError:
        return False
    return True


def release(work_key: str) -> list[str]:
    """Release everything the unit `work_key` claims; the addresses."""
    return _release(work_key, lambda claim: claim == work_key)


def sweep(claw: str) -> list[str]:
    """Release everything any of `claw`'s units claims; the addresses."""
    return _release(claw, lambda claim: claim.startswith(f'{claw}/'))


def _release(name: str, held: Callable[[str], bool]) -> list[str]:
    # Filters fold case and `~` is a substring match, so filter exactly here.
    released: list[str] = []
    for task in store.listed(f'namespace={store.NAMESPACE},claim~{name}'):
        holder = str(task['claim'])
        if held(holder):
            agent = ops.Writer(schema.Actor.agent(holder))
            agent.release(schema.Ref.parse(store.ref(task)))
            released.append(store.ref(task))
    return released
