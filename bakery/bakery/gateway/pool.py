"""
Live pi children, one per unit of work, kept warm between runs.

A child is reused while its unit keeps getting runs and closed after the
claw's idle timeout; resuming a closed unit just starts pi again with the
same session id, which restores the session from disk.
"""

import asyncio
import contextlib
import dataclasses
import logging
import os
import time

from .. import secrets
from .. import state
from . import config
from . import rpc

REAP_INTERVAL = 15.0
# The only parts of the gateway's environment a claw sees, besides its
# configured secrets.
INHERITED_ENV = ('PATH', 'HOME', 'USER', 'LANG', 'TMPDIR', 'TASK_FOLDER')
INHERITED_ENV_PREFIXES = ('XDG_', 'LC_')

log = logging.getLogger(__name__)


def session_id(work_key: str) -> str:
    return work_key.replace('/', '-')


def bakery_dir() -> str:
    return str(state.root() / '_gateway' / 'bakery')


def child_env(claw: config.Claw) -> dict[str, str]:
    """Raises secrets.SecretError if a configured secret is missing."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in INHERITED_ENV or name.startswith(INHERITED_ENV_PREFIXES)
    }
    env.update({name: secrets.get(name) for name in claw.secrets})
    env.update(
        {
            'PI_CODING_AGENT_DIR': str(claw.profile),
            'PI_CODING_AGENT_BAKERY_DIR': bakery_dir(),
            'BAKERY_ASK_POLICY': claw.ask.policy,
            'BAKERY_ASK_TIMEOUT_HOURS': f'{claw.ask.timeout_hours:g}',
            'BAKERY_ASK_ON_TIMEOUT': claw.ask.on_timeout,
        }
    )
    return env


@dataclasses.dataclass
class Child:
    proc: rpc.PiProcess
    idle_exit_seconds: float
    # None while a run is using the child.
    idle_since: float | None = None


class Pool:
    def __init__(self) -> None:
        self.children: dict[str, Child] = {}
        self._closing: set[asyncio.Task[None]] = set()
        self._reaper: asyncio.Task[None] | None = None

    def start(self) -> None:
        self._reaper = asyncio.create_task(self._reap())

    def __len__(self) -> int:
        return len(self.children)

    def has(self, work_key: str) -> bool:
        child = self.children.get(work_key)
        return child is not None and child.proc.alive

    async def acquire(self, claw: config.Claw, work_key: str) -> rpc.PiProcess:
        child = self.children.get(work_key)
        if child is not None and child.proc.alive:
            child.idle_since = None
            return child.proc
        sid = session_id(work_key)
        claw.cwd.mkdir(parents=True, exist_ok=True)
        claw.sessions_dir.mkdir(parents=True, exist_ok=True)
        proc = await rpc.PiProcess.start(
            [
                '--session-id', sid,
                '--session-dir', str(claw.sessions_dir),
                '--name', sid,
                '--model', claw.model,
                '--thinking', claw.thinking,
            ],
            cwd=claw.cwd,
            env=child_env(claw),
        )  # fmt: skip
        self.children[work_key] = Child(
            proc, claw.concurrency.idle_exit_minutes * 60
        )
        return proc

    def release(self, work_key: str) -> None:
        child = self.children.get(work_key)
        if child is None:
            return
        if child.proc.alive:
            child.idle_since = time.monotonic()
        else:
            del self.children[work_key]

    def evict_idle(self) -> bool:
        """Close the longest-idle child to free a process slot."""
        idle = [
            (child.idle_since, key)
            for key, child in self.children.items()
            if child.idle_since is not None
        ]
        if not idle:
            return False
        self._close(min(idle)[1])
        return True

    def _close(self, work_key: str) -> None:
        child = self.children.pop(work_key)
        task = asyncio.create_task(child.proc.close())
        self._closing.add(task)
        task.add_done_callback(self._closing.discard)

    async def _reap(self) -> None:
        while True:
            await asyncio.sleep(REAP_INTERVAL)
            now = time.monotonic()
            for key, child in list(self.children.items()):
                if not child.proc.alive and child.idle_since is not None:
                    del self.children[key]
                elif (
                    child.idle_since is not None
                    and now - child.idle_since > child.idle_exit_seconds
                ):
                    log.info('closing idle %s', key)
                    self._close(key)

    async def close(self) -> None:
        if self._reaper:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
        for key in list(self.children):
            self._close(key)
        if self._closing:
            await asyncio.gather(*self._closing, return_exceptions=True)
