"""
Gateway and claw configuration.

`claws/gateway.toml` holds the gateway's own settings, which no claw can
override, among them every claw's task list rules (`[tasks.<claw>]`, in
task's `task.rules` format); `claws/defaults.toml` holds every claw's
defaults, and each `claws/<name>/claw.toml` overrides any of those and adds
its jobs. The model and thinking level have no default: every claw sets both,
and a job may override either. Loading is strict: unknown keys and bad values
raise ConfigError, so a typo can never silently change a limit.
"""

import dataclasses
import datetime
import pathlib
import re
import tomllib
import zoneinfo
from collections.abc import Mapping
from typing import Any

import croniter
from task import rules as rules_
from task import schema

from .. import collectors
from .. import paths
from .. import state
from . import policy as policy_
from . import toml

GATEWAY_NAME = 'gateway.toml'
DEFAULTS_NAME = 'defaults.toml'
CLAW_NAME = 'claw.toml'
ConfigError = toml.ConfigError
DEFAULT_DEBOUNCE_SECONDS = 120.0
NAME_RE = re.compile(r'[a-z][a-z0-9-]*')
# pi's levels; pi, not this check, clamps a level the model does not support.
THINKING_LEVELS = ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
# macOS caps a unix socket path at 104 bytes, its terminating NUL included.
MAX_SOCKET_PATH = 103
# Session ids are work keys with `/` as `-`: `<claw>-<job>` plus a stamp, or
# `<claw>-` plus a fixed suffix, the longest of which is a Discord thread's
# (`thread-` and a snowflake of up to 20 digits).
STAMP_LENGTH = len('-YYYYmmdd-HHMMSS')
LONGEST_FIXED_SUFFIX = len('thread-') + 20


@dataclasses.dataclass(frozen=True)
class Limits:
    cost_usd: float
    minutes: float
    turns: int
    daily_cost_usd: float


@dataclasses.dataclass(frozen=True)
class Concurrency:
    max_active: int
    max_resumes: int
    idle_exit_minutes: float


@dataclasses.dataclass(frozen=True)
class ActiveHours:
    start: datetime.time
    end: datetime.time

    def contains(self, moment: datetime.time) -> bool:
        if self.start <= self.end:
            return self.start <= moment < self.end
        # Wraps past midnight, eg. 22:00-06:00.
        return moment >= self.start or moment < self.end


@dataclasses.dataclass(frozen=True)
class Job:
    # pylint: disable=too-many-instance-attributes
    name: str
    # None: the job only runs when triggered (eg. `learn`, by `bakery teach`).
    cron: str | None
    prompt: str
    active_hours: ActiveHours | None
    silent_ok: bool
    # Overrides for this job's units of work.
    model: str | None
    thinking: str | None
    # A bakery.collectors name: gathers input first; nothing found, no run,
    # unless skip_empty is false (then the job's prompt runs on its own).
    collector: str | None
    skip_empty: bool
    # After a run settles, start the next (until the collector finds
    # nothing): for working through a queue.
    repeat: bool
    # Also run when files under this path change, once quiet for
    # debounce_seconds.
    watch: pathlib.Path | None
    debounce_seconds: float
    # Defined in defaults.toml for every claw, rather than by this claw.
    shared: bool


@dataclasses.dataclass(frozen=True)
class Memory:
    # Flush memory once the context is this close to pi's compaction point.
    flush_margin_tokens: int
    # The largest MEMORY.md may grow; keep it within its context.toml budget.
    max_chars: int


@dataclasses.dataclass(frozen=True)
class Git:
    # The identity the claw's commits carry: the claws' GitHub user's.
    user_name: str
    user_email: str


@dataclasses.dataclass(frozen=True)
class Claw:
    # pylint: disable=too-many-instance-attributes
    name: str
    profile: pathlib.Path
    enabled: bool
    model: str
    thinking: str
    cwd: pathlib.Path
    limits: Limits
    concurrency: Concurrency
    memory: Memory
    # Environment variable name -> Keychain item (see bakery/secrets.py).
    secrets: Mapping[str, str]
    # The secrets bash is handed too, by variable name.
    bash_secrets: tuple[str, ...]
    git: Git
    policy: policy_.Policy
    # What it may do to the task list, from gateway.toml; nothing if unset.
    rules: rules_.Rules
    jobs: tuple[Job, ...]

    @property
    def state_dir(self) -> pathlib.Path:
        return state.root() / self.name

    @property
    def sessions_dir(self) -> pathlib.Path:
        return self.state_dir / 'sessions'

    @property
    def pi_state_dir(self) -> pathlib.Path:
        """Its pi children's $PI_CODING_AGENT_STATE_DIR, apart from others'."""
        return state.claw_pi_states() / self.name

    def job(self, name: str) -> Job:
        for job in self.jobs:
            if job.name == name:
                return job
        raise ConfigError(f'{self.name} has no job {name!r}')


@dataclasses.dataclass(frozen=True)
class Discord:
    guild_id: int
    operator_id: int


@dataclasses.dataclass(frozen=True)
class Gateway:
    timezone: zoneinfo.ZoneInfo
    max_processes: int
    daily_cost_usd: float
    # None without a [discord] table; only the gateway needs it.
    discord: Discord | None


@dataclasses.dataclass(frozen=True)
class Config:
    gateway: Gateway
    claws: Mapping[str, Claw]

    def claw(self, name: str) -> Claw:
        if name not in self.claws:
            raise ConfigError(f'unknown claw: {name}')
        return self.claws[name]


def _merge(
    base: Mapping[str, Any], override: Mapping[str, Any]
) -> dict[str, Any]:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _active_hours(raw: str, where: str) -> ActiveHours:
    match = re.fullmatch(r'(\d\d:\d\d)-(\d\d:\d\d)', raw)
    if not match:
        raise ConfigError(f'{where}.active_hours: expected HH:MM-HH:MM')
    try:
        start, end = (datetime.time.fromisoformat(x) for x in match.groups())
    except ValueError as e:
        raise ConfigError(f'{where}.active_hours: {e}') from None
    return ActiveHours(start, end)


def _collector(table: toml.Table) -> str | None:
    if not table.optional('collector'):
        return None
    name = table.string('collector', tuple(collectors.COLLECTORS))
    return name


def _skip_empty(table: toml.Table) -> bool:
    if not table.optional('skip_empty'):
        return True
    if not table.optional('collector'):
        raise ConfigError(f'{table.where}.skip_empty: needs a collector')
    return table.boolean('skip_empty')


def _cron(table: toml.Table) -> str | None:
    if not table.optional('cron'):
        if table.optional('active_hours'):
            raise ConfigError(f'{table.where}.active_hours: needs a cron')
        return None
    cron = table.string('cron')
    if not croniter.croniter.is_valid(cron):
        raise ConfigError(f'{table.where}.cron: invalid expression')
    return cron


def _job(table: toml.Table, names: set[str], shared: set[str]) -> Job:
    name = table.string('name')
    if not NAME_RE.fullmatch(name) or name in names:
        raise ConfigError(f'{table.where}.name: invalid or duplicate')
    names.add(name)
    job = Job(
        name=name,
        cron=_cron(table),
        prompt=table.string('prompt'),
        active_hours=(
            _active_hours(table.string('active_hours'), table.where)
            if table.optional('active_hours')
            else None
        ),
        silent_ok=table.boolean('silent_ok')
        if table.optional('silent_ok')
        else False,
        model=table.string('model') if table.optional('model') else None,
        thinking=(
            table.string('thinking', THINKING_LEVELS)
            if table.optional('thinking')
            else None
        ),
        collector=_collector(table),
        skip_empty=_skip_empty(table),
        repeat=table.boolean('repeat') if table.optional('repeat') else False,
        watch=(
            toml.expand(table.string('watch'), pathlib.Path.home())
            if table.optional('watch')
            else None
        ),
        debounce_seconds=(
            table.positive('debounce_seconds')
            if table.optional('debounce_seconds')
            else DEFAULT_DEBOUNCE_SECONDS
        ),
        shared=name in shared,
    )
    table.done()
    return job


def _bash_secrets(
    table: toml.Table, secrets: Mapping[str, str]
) -> tuple[str, ...]:
    names = table.strings('bash_secrets')
    for name in names:
        if name not in secrets:
            raise ConfigError(
                f'{table.where}.bash_secrets: {name} is not in its secrets'
            )
    return names


def _jobs(table: toml.Table, shared_jobs: set[str]) -> tuple[Job, ...]:
    raw = table.data.get('job', [])
    table.used.add('job')
    if not isinstance(raw, list):
        raise ConfigError(f'{table.where}.job: must be an array of tables')
    names: set[str] = set()
    return tuple(
        _job(toml.Table(job, f'{table.where}.job[{i}]'), names, shared_jobs)
        for i, job in enumerate(raw)
    )


def _git(table: toml.Table) -> Git:
    git = Git(
        user_name=table.string('user_name'),
        user_email=table.string('user_email'),
    )
    table.done()
    return git


def _claw(
    name: str,
    profile: pathlib.Path,
    data: Mapping[str, Any],
    shared_jobs: set[str],
    rules: rules_.Rules,
) -> Claw:
    table = toml.Table(data, name)
    limits = table.table('limits')
    concurrency = table.table('concurrency')
    memory = table.table('memory')
    secrets = table.secrets('secrets')
    jobs = _jobs(table, shared_jobs)
    cwd = (
        toml.expand(table.string('cwd'), profile)
        if table.optional('cwd')
        else state.root() / name
    )
    claw = Claw(
        name=name,
        profile=profile,
        enabled=table.boolean('enabled'),
        model=table.string('model'),
        thinking=table.string('thinking', THINKING_LEVELS),
        cwd=cwd,
        limits=Limits(
            cost_usd=limits.positive('cost_usd'),
            minutes=limits.positive('minutes'),
            turns=limits.count('turns'),
            daily_cost_usd=limits.positive('daily_cost_usd'),
        ),
        concurrency=Concurrency(
            max_active=concurrency.count('max_active'),
            max_resumes=concurrency.count('max_resumes', minimum=0),
            idle_exit_minutes=concurrency.positive('idle_exit_minutes'),
        ),
        secrets=secrets,
        bash_secrets=_bash_secrets(table, secrets),
        git=_git(table.table('git')),
        policy=policy_.parse(table.table('policy'), profile),
        rules=rules,
        memory=Memory(
            flush_margin_tokens=memory.count('flush_margin_tokens', minimum=0),
            max_chars=memory.count('max_chars'),
        ),
        jobs=jobs,
    )
    for sub in (limits, concurrency, memory, table):
        sub.done()
    _check_socket_paths(claw)
    return claw


def _check_socket_paths(claw: Claw) -> None:
    """
    Refuse a claw whose sessions' sockets could not be bound: pi's registry
    sockets, and the gateway's task list sockets.
    """
    suffix = max(
        (len(job.name) + STAMP_LENGTH for job in claw.jobs), default=0
    )
    session_id = 'x' * (len(claw.name) + 1 + max(suffix, LONGEST_FIXED_SUFFIX))
    registry = paths.registry(claw.pi_state_dir) / f'{session_id}.sock'
    length = max(len(str(registry)), len(str(state.task_socket(session_id))))
    if length > MAX_SOCKET_PATH:
        raise ConfigError(
            f'{claw.name}: its session sockets need {length} bytes, over the'
            f' {MAX_SOCKET_PATH}-byte limit; shorten its name, its job names,'
            ' or $XDG_STATE_HOME'
        )


def _gateway(data: Mapping[str, Any]) -> Gateway:
    table = toml.Table(data, 'gateway')
    try:
        tz = zoneinfo.ZoneInfo(table.string('timezone'))
    except zoneinfo.ZoneInfoNotFoundError:
        raise ConfigError('gateway.timezone: unknown timezone') from None
    discord = None
    if table.optional('discord'):
        sub = table.table('discord')
        discord = Discord(
            guild_id=sub.count('guild_id'),
            operator_id=sub.count('operator_id'),
        )
        sub.done()
    gateway = Gateway(
        timezone=tz,
        max_processes=table.count('max_processes'),
        daily_cost_usd=table.positive('daily_cost_usd'),
        discord=discord,
    )
    table.done()
    return gateway


def _read(path: pathlib.Path) -> dict[str, Any]:
    try:
        return tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise ConfigError(f'{path}: {e}') from None


def _merge_jobs(defaults: object, own: object) -> list[object]:
    """
    Default jobs apply to every claw; a claw's job replaces the default of
    the same name.
    """
    if not isinstance(defaults, list) or not isinstance(own, list):
        raise ConfigError('job: must be an array of tables')
    names = {job.get('name') for job in own if isinstance(job, dict)}
    kept = [
        job
        for job in defaults
        if not isinstance(job, dict) or job.get('name') not in names
    ]
    return [*kept, *own]


def _rules(data: object) -> dict[str, rules_.Rules]:
    if not isinstance(data, dict):
        raise ConfigError('gateway.tasks: must be a table')
    try:
        return rules_.parse(data)
    except schema.TaskError as e:
        raise ConfigError(f'gateway.tasks: {e}') from None


def load(claws_dir: pathlib.Path) -> Config:
    settings = _read(claws_dir / GATEWAY_NAME)
    rules = _rules(settings.pop('tasks', {}))
    gateway = _gateway(settings)
    defaults = _read(claws_dir / DEFAULTS_NAME)
    claws: dict[str, Claw] = {}
    for path in sorted(claws_dir.glob(f'*/{CLAW_NAME}')):
        name = path.parent.name
        if not NAME_RE.fullmatch(name):
            raise ConfigError(f'{path}: invalid claw name {name!r}')
        own = _read(path)
        data = _merge(defaults, own)
        data['job'] = _merge_jobs(defaults.get('job', []), own.get('job', []))
        own_names = {
            job.get('name')
            for job in own.get('job', [])
            if isinstance(job, dict)
        }
        shared = {
            job.get('name')
            for job in defaults.get('job', [])
            if isinstance(job, dict) and job.get('name') not in own_names
        }
        claws[name] = _claw(
            name, path.parent, data, shared, rules.get(name, rules_.Rules())
        )
    if unknown := set(rules) - set(claws):
        raise ConfigError(
            f'gateway.tasks: rules for unknown claws {sorted(unknown)}'
        )
    return Config(gateway=gateway, claws=claws)
