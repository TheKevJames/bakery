"""
Live pi children, one per unit of work, kept warm between runs.

A child is reused while its unit keeps getting runs and closed after the
claw's idle timeout; resuming a closed unit just starts pi again with the
same session id, which restores the session from disk.
"""

import asyncio
import contextlib
import dataclasses
import json
import logging
import os
import pathlib
import sysconfig
import time

from task import api

from .. import paths
from .. import secrets
from .. import state
from ..task_list import store
from . import config
from . import policy
from . import rpc
from . import sandbox

REAP_INTERVAL = 15.0
# The only parts of the gateway's environment a claw sees, besides its
# configured secrets. The launcher sets $TMPDIR.
INHERITED_ENV = ('PATH', 'HOME', 'USER', 'LANG', 'TASK_FOLDER')
INHERITED_ENV_PREFIXES = ('XDG_', 'LC_')
# Pointed into the claw's own home, so tools find an empty config (and keep
# their caches, and what eg. `uv tool install` installs) instead of failing
# on the unreadable or unwritable real ones under $HOME. Go ignores XDG and
# defaults to ~/go and ~/Library/Caches/go-build.
HOME_DIRS = {
    'XDG_CONFIG_HOME': 'config',
    'XDG_CACHE_HOME': 'cache',
    'XDG_DATA_HOME': 'data',
    'XDG_BIN_HOME': 'bin',
    'GOPATH': 'data/go',
    'GOCACHE': 'cache/go-build',
}

log = logging.getLogger(__name__)


def session_id(work_key: str) -> str:
    return work_key.replace('/', '-')


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


def _task_bin() -> pathlib.Path:
    """
    A dir holding only the gateway's own `task`, first on every claw's PATH
    so it speaks the task server's protocol whatever the operator installed.
    The rest of the gateway's scripts (its python among them) stay out.
    """
    path = state.root() / '_gateway' / 'bin'
    link = path / 'task'
    target = pathlib.Path(sysconfig.get_path('scripts')) / 'task'
    if not link.is_symlink() or link.readlink() != target:
        path.mkdir(parents=True, exist_ok=True)
        link.unlink(missing_ok=True)
        link.symlink_to(target)
    return path


def child_env(claw: config.Claw, sid: str) -> dict[str, str]:
    """Raises secrets.SecretError if a configured secret is missing."""
    env = {
        name: value
        for name, value in os.environ.items()
        if name in INHERITED_ENV or name.startswith(INHERITED_ENV_PREFIXES)
    }
    env.update(
        {
            name: str(sandbox.home(claw) / sub)
            for name, sub in HOME_DIRS.items()
        }
    )
    # The claw's own bin after the gateway's `task`, so what it installs
    # cannot shadow it.
    path = (str(_task_bin()), env['XDG_BIN_HOME'], env.get('PATH'))
    env['PATH'] = os.pathsep.join(filter(None, path))
    env.update(
        {name: secrets.get(item) for name, item in claw.secrets.items()}
    )
    env.update(
        {
            'BAKERY_SANDBOX': json.dumps(
                sandbox.settings(claw, env.get('PATH', ''), sid)
            ),
            'BAKERY_SANDBOX_VIOLATIONS': str(sandbox.violations(claw, sid)),
            'BAKERY_SANDBOX_TMP': str(sandbox.tmp(claw)),
            'GIT_CONFIG_GLOBAL': str(sandbox.git_config(claw)),
            # Node's fetch (pi's model and web clients) ignores the sandbox's
            # proxy otherwise, and the sandbox refuses direct connections.
            'NODE_USE_ENV_PROXY': '1',
            'BAKERY_POLICY': policy.serialize(
                secret_names=set(claw.secrets) - set(claw.bash_secrets)
            ),
            'BAKERY_CLAW': claw.name,
            'BAKERY_TASK_NAMESPACE': store.NAMESPACE,
            # Where its `task` sends every command (see task_server).
            api.SERVER_ENV: str(state.task_socket(sid)),
            'PI_CODING_AGENT_DIR': str(claw.profile),
            'PI_CODING_AGENT_STATE_DIR': str(claw.pi_state_dir),
            'BAKERY_MEMORY_DIR': str(claw.state_dir),
            # Names the unit's worktrees (pi/claw-rules/repos.md).
            'BAKERY_SESSION_ID': sid,
            'BAKERY_SHARED_DIR': str(state.root() / state.SHARED),
            'BAKERY_MEMORY_MAX_CHARS': str(claw.memory.max_chars),
            'BAKERY_FLUSH_MARGIN_TOKENS': str(claw.memory.flush_margin_tokens),
        }
    )
    return env


def child_spec(
    claw: config.Claw, work_key: str, job: config.Job | None
) -> tuple[tuple[str, ...], dict[str, str]]:
    """The command and environment a child for `work_key` starts with."""
    sid = session_id(work_key)
    model = job.model if job and job.model else claw.model
    thinking = job.thinking if job and job.thinking else claw.thinking
    command = (
        'node', str(sandbox.launcher()),
        'pi', '--mode', 'rpc',
        '--session-id', sid,
        '--session-dir', str(claw.sessions_dir),
        '--name', sid,
        '--model', model,
        '--thinking', thinking,
        # Loaded by the gateway, not the profile, so no profile can run a
        # claw without its policy.
        '--extension', str(policy_extension()),
    )  # fmt: skip
    return command, child_env(claw, sid)


@dataclasses.dataclass
class Child:
    proc: rpc.PiProcess
    idle_exit_seconds: float
    # What it was started with: its command and environment, which carry its
    # sandbox and policy.
    spec: tuple[tuple[str, ...], dict[str, str]]
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
        """
        A child for `work_key`; `job` (its unit's job) sets overrides. A live
        child started with a different spec (eg. a reloaded policy) is
        replaced; the new one resumes the session from disk.
        """
        spec = child_spec(claw, work_key, job)
        child = self.children.get(work_key)
        if child is not None and child.proc.alive:
            if child.spec == spec:
                child.idle_since = None
                return child.proc
            log.info('restarting %s: its sandbox or policy changed', work_key)
            await self.children.pop(work_key).proc.close()
        claw.sessions_dir.mkdir(parents=True, exist_ok=True)
        sandbox.prepare(claw)
        command, env = spec
        proc = await rpc.PiProcess.start(command, cwd=claw.cwd, env=env)
        await _check_startup(proc)
        self.children[work_key] = Child(
            proc, claw.concurrency.idle_exit_minutes * 60, spec
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
