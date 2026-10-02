"""Plain-text rendering for Discord and the CLI."""

import time
from collections.abc import Mapping
from typing import Any

from ..gateway import channel
from ..gateway import ledger

MESSAGE_LIMIT = 2000
# Longer replies are attached as a file rather than flooding the thread.
MAX_CHUNKS = 4
OUTCOME_ICONS = {
    ledger.Status.settled: '✅',
    ledger.Status.parked: '⏸️',
    ledger.Status.failed: '❌',
    ledger.Status.interrupted: '⚠️',
}
NEEDS_ATTENTION = frozenset(
    {ledger.Status.parked, ledger.Status.failed, ledger.Status.interrupted}
)


def clip(text: str, limit: int = MESSAGE_LIMIT) -> str:
    return text if len(text) <= limit else f'{text[: limit - 1]}…'


def elapsed(since: float) -> str:
    seconds = int(time.time() - since)
    return f'{seconds // 60}m{seconds % 60:02d}s'


def _stats(info: channel.RunInfo) -> str:
    spent = f'${info.cost_usd:.2f}'
    return f'turn {info.turns} · {spent} · {elapsed(info.started_at)}'


def progress(info: channel.RunInfo) -> str:
    doing = info.current_tool or 'thinking'
    return f'⏳ {info.work_key} · {doing} · {_stats(info)}'


def outcome(info: channel.RunInfo) -> str:
    icon = OUTCOME_ICONS.get(info.status, '•')
    reason = f' — {info.reason}' if info.reason else ''
    return clip(
        f'{icon} {info.work_key} {info.status}{reason} · {_stats(info)}'
    )


def chunks(text: str) -> list[str] | None:
    """Split on line boundaries into messages; None if it needs a file."""
    out: list[str] = []
    current = ''
    for line in text.splitlines(keepends=True):
        while len(line) > MESSAGE_LIMIT:
            if current:
                out.append(current)
                current = ''
            out.append(line[:MESSAGE_LIMIT])
            line = line[MESSAGE_LIMIT:]
        if len(current) + len(line) > MESSAGE_LIMIT:
            out.append(current)
            current = ''
        current += line
    if current.strip():
        out.append(current)
    return out if len(out) <= MAX_CHUNKS else None


def _run_line(run: Mapping[str, Any]) -> str:
    tool = f' [{run["current_tool"]}]' if run['current_tool'] else ''
    return (
        f'    {run["work_key"]}{tool}  {run["turns"]} turns'
        f'  ${run["cost_usd"]:.2f}  {elapsed(run["started_at"])}'
    )


def status(data: Mapping[str, Any]) -> str:
    lines = [f'processes: {data["processes"]}']
    for name, claw in data['claws'].items():
        state = 'enabled' if claw['enabled'] else 'disabled'
        state = 'paused' if claw['paused'] else state
        lines.append(f'{name}: {state}')
        lines.extend(_run_line(run) for run in claw['active'])
        lines.extend(f'    {key} (queued)' for key in claw['queued'])
        lines.extend(
            f'    next {job}: {due}'
            for job, due in sorted(claw['next'].items())
        )
    return '\n'.join(lines)


def budget(data: Mapping[str, Any]) -> str:
    rows = [('all', data['global']), *sorted(data['claws'].items())]
    width = max(len(name) for name, _ in rows)
    return '\n'.join(
        f'{name:<{width}}  ${row["spent_usd"]:.2f}'
        f' / ${row["limit_usd"]:.2f} today'
        for name, row in rows
    )
