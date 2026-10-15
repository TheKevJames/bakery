"""
Teaching claws: `bakery teach <claw|all> "<lesson>"` runs each claw's `learn`
job (claws/defaults.toml, pi/claw-prompts/learn.md) with the operator's
lesson appended, then reports the run's reply and what it changed in
MEMORY.md, from the run's state commit rather than the model's account.

`start` queues the runs and returns their work keys at once, so the CLI can
say what is running before it waits; `result` then waits for one. Waiting is
set up before a run is queued, so even one that stops at once (eg. out of
budget) is reported.
"""

import asyncio
from typing import Any

from .. import state
from . import channel as channel_
from . import core
from . import jobs

JOB = 'learn'


class Teacher:
    def __init__(self, gateway: core.Gateway) -> None:
        self.gateway = gateway
        # Work key -> its run's outcome, for lessons someone will wait on.
        self._pending: dict[str, asyncio.Future[channel_.RunInfo]] = {}

    def start(
        self, target: str, lesson: str, *, wait: bool
    ) -> dict[str, dict[str, str]]:
        """Queue the lesson for `target` (a claw, or every enabled one)."""
        if not lesson.strip():
            raise core.GatewayError('the lesson is empty')
        config = self.gateway.config
        if target == 'all':
            claws = [c for c in config.claws.values() if c.enabled]
        else:
            claws = [config.claw(target)]
            if not claws[0].enabled:
                raise core.GatewayError(f'{target} is disabled')
        started: dict[str, str] = {}
        skipped: dict[str, str] = {}
        for claw in claws:
            if {'all', claw.name} & self.gateway.paused:
                if target != 'all':
                    raise core.GatewayError(f'{claw.name} is paused')
                skipped[claw.name] = 'paused'
                continue
            job = claw.job(JOB)
            work_key = f'{claw.name}/{JOB}-{jobs.stamp()}'
            if work_key in self._pending:
                raise core.GatewayError(
                    f'{claw.name} was taught this second; try again'
                )
            if wait:
                self._pending[work_key] = self.gateway.finished(work_key)
            self.gateway.jobs.start(
                claw,
                job,
                'manual',
                work_key=work_key,
                prompt=f'{job.prompt}\n\n<lesson>\n{lesson}\n</lesson>',
            )
            started[claw.name] = work_key
        return {'started': started, 'skipped': skipped}

    async def result(self, work_key: str) -> dict[str, Any]:
        """Wait for a lesson `start` was asked to wait on."""
        future = self._pending.get(work_key)
        if future is None:
            raise core.GatewayError(f'no lesson to wait on for {work_key}')
        info = await asyncio.shield(future)
        self._pending.pop(work_key, None)
        diff = (
            await asyncio.to_thread(_memory_diff, info.claw, info.state_commit)
            if info.state_commit
            else ''
        )
        return {
            'work_key': work_key,
            'status': str(info.status),
            'reason': info.reason,
            'reply': info.final_text,
            'diff': diff,
        }


def _memory_diff(claw: str, commit: str) -> str:
    return state.git(
        'show', '--format=', commit, '--', f'{claw}/MEMORY.md'
    ).stdout
