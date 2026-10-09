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
import pathlib
import time

from .. import paths
from .. import secrets
from .. import state
from . import config
from . import policy
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


def policy_extension() -> pathlib.Path:
    return paths.repo() / 'pi' / 'claw-extensions' / 'policy'


async def _check_startup(proc: rpc.PiProcess) -> None:
    """
    Fail fast if an extension failed while pi started (eg. a bad policy or
    context.toml), rather than running a claw without it.
    """
    await proc.command({'type': 'get_state'})
    errors = proc.extension_errors()
    if errors:
        await proc.close()
        raise rpc.RpcError('; '.join(errors))


def child_env(claw: config.Claw, job: config.Job | None) -> dict[str, str]:
    """Raises secrets.SecretError if a configured secret is missing."""
    tools = job.tools if job is not None else ()
    claw_policy = dataclasses.replace(
        claw.policy, tools=(*claw.policy.tools, *tools)
    )
    env = {
        name: value
        for name, value in os.environ.items()
        if name in INHERITED_ENV or name.startswith(INHERITED_ENV_PREFIXES)
    }
    env.update(
        {name: secrets.get(item) for name, item in claw.secrets.items()}
    )
    env.update(
        {
            # Unless their policy's allow_read says otherwise, claws may read
            # neither the gateway's files nor each other's state (memory,
            # transcripts, the state repo's history).
            'BAKERY_POLICY': policy.serialize(
                claw_policy,
                extra_deny_read=[state.root()],
                extra_allow_read=[claw.state_dir, state.root() / state.SHARED],
                secret_names=claw.secrets,
            ),
            'BAKERY_CLAW': claw.name,
            'BAKERY_TASK_TAG': claw.policy.task_tag,
            'PI_CODING_AGENT_DIR': str(claw.profile),
            'PI_CODING_AGENT_BAKERY_DIR': bakery_dir(),
            'BAKERY_ASK_POLICY': claw.ask.policy,
            'BAKERY_ASK_TIMEOUT_HOURS': f'{claw.ask.timeout_hours:g}',
            'BAKERY_ASK_ON_TIMEOUT': claw.ask.on_timeout,
            'BAKERY_MEMORY_DIR': str(claw.state_dir),
            'BAKERY_SHARED_DIR': str(state.root() / state.SHARED),
            'BAKERY_MEMORY_MAX_CHARS': str(claw.memory.max_chars),
            'BAKERY_FLUSH_MARGIN_TOKENS': str(claw.memory.flush_margin_tokens),
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

    async def acquire(
        self, claw: config.Claw, work_key: str, job: config.Job | None
    ) -> rpc.PiProcess:
        """A child for `work_key`; `job` (its unit's job) sets overrides."""
        child = self.children.get(work_key)
        if child is not None and child.proc.alive:
            child.idle_since = None
            return child.proc
        sid = session_id(work_key)
        claw.cwd.mkdir(parents=True, exist_ok=True)
        claw.sessions_dir.mkdir(parents=True, exist_ok=True)
        model = job.model if job and job.model else claw.model
        thinking = job.thinking if job and job.thinking else claw.thinking
        proc = await rpc.PiProcess.start(
            [
                '--session-id', sid,
                '--session-dir', str(claw.sessions_dir),
                '--name', sid,
                '--model', model,
                '--thinking', thinking,
                # Loaded by the gateway, not the profile, so no profile can
                # run a claw without its policy.
                '--extension', str(policy_extension()),
            ],
            cwd=claw.cwd,
            env=child_env(claw, job),
        )  # fmt: skip
        await _check_startup(proc)
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
