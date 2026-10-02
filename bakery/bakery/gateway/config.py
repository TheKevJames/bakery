"""
Gateway and claw configuration.

`claws/defaults.toml` holds a `[gateway]` table and a `[claw]` table of
defaults; each `claws/<name>/claw.toml` overrides any of those defaults and
adds its jobs. Loading is strict: unknown keys and bad values raise
ConfigError, so a typo can never silently change a limit.
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

from .. import collectors
from .. import state
from . import policy as policy_
from . import toml

DEFAULTS_NAME = 'defaults.toml'
CLAW_NAME = 'claw.toml'
ConfigError = toml.ConfigError
DEFAULT_DEBOUNCE_SECONDS = 120.0
NAME_RE = re.compile(r'[a-z][a-z0-9-]*')
ASK_POLICIES = ('ask', 'assume', 'park')
TIMEOUT_POLICIES = ('assume', 'park')


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
class Ask:
    policy: str
    timeout_hours: float
    on_timeout: str


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
    cron: str
    prompt: str
    active_hours: ActiveHours | None
    silent_ok: bool
    # Overrides for this job's units of work; tools are added to the policy.
    model: str | None
    thinking: str | None
    tools: tuple[str, ...]
    # A bakery.collectors name: gathers input first; nothing found, no run.
    collector: str | None
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
    ask: Ask
    memory: Memory
    # Environment variable name -> Keychain item (see bakery/secrets.py).
    secrets: Mapping[str, str]
    policy: policy_.Policy
    jobs: tuple[Job, ...]

    @property
    def state_dir(self) -> pathlib.Path:
        return state.root() / self.name

    @property
    def sessions_dir(self) -> pathlib.Path:
        return self.state_dir / 'sessions'

    def job(self, name: str) -> Job:
        for job in self.jobs:
            if job.name == name:
                return job
        raise ConfigError(f'{self.name} has no job {name!r}')


@dataclasses.dataclass(frozen=True)
class Discord:
    guild_id: int
    owner_id: int


@dataclasses.dataclass(frozen=True)
class Gateway:
    timezone: zoneinfo.ZoneInfo
    max_processes: int
    daily_cost_usd: float
    # None without a [gateway.discord] table; only the gateway needs it.
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


def _job(table: toml.Table, names: set[str], shared: set[str]) -> Job:
    name = table.string('name')
    if not NAME_RE.fullmatch(name) or name in names:
        raise ConfigError(f'{table.where}.name: invalid or duplicate')
    names.add(name)
    cron = table.string('cron')
    if not croniter.croniter.is_valid(cron):
        raise ConfigError(f'{table.where}.cron: invalid expression')
    job = Job(
        name=name,
        cron=cron,
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
            table.string('thinking') if table.optional('thinking') else None
        ),
        tools=table.strings('tools') if table.optional('tools') else (),
        collector=_collector(table),
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


def _claw(
    name: str,
    profile: pathlib.Path,
    data: Mapping[str, Any],
    shared_jobs: set[str],
) -> Claw:
    table = toml.Table(data, name)
    limits = table.table('limits')
    concurrency = table.table('concurrency')
    ask = table.table('ask')
    memory = table.table('memory')
    jobs_raw = data.get('job', [])
    table.used.add('job')
    if not isinstance(jobs_raw, list):
        raise ConfigError(f'{name}.job: must be an array of tables')
    names: set[str] = set()
    jobs = tuple(
        _job(toml.Table(job, f'{name}.job[{i}]'), names, shared_jobs)
        for i, job in enumerate(jobs_raw)
    )
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
        thinking=table.string('thinking'),
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
        secrets=table.secrets('secrets'),
        policy=policy_.parse(table.table('policy'), profile),
        ask=Ask(
            policy=ask.string('policy', ASK_POLICIES),
            timeout_hours=ask.positive('timeout_hours'),
            on_timeout=ask.string('on_timeout', TIMEOUT_POLICIES),
        ),
        memory=Memory(
            flush_margin_tokens=memory.count('flush_margin_tokens', minimum=0),
            max_chars=memory.count('max_chars'),
        ),
        jobs=jobs,
    )
    for sub in (limits, concurrency, ask, memory, table):
        sub.done()
    return claw


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
            guild_id=sub.count('guild_id'), owner_id=sub.count('owner_id')
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


def load(claws_dir: pathlib.Path) -> Config:
    defaults = _read(claws_dir / DEFAULTS_NAME)
    unknown = set(defaults) - {'gateway', 'claw'}
    if unknown:
        raise ConfigError(f'{DEFAULTS_NAME}: unknown keys {sorted(unknown)}')
    claws: dict[str, Claw] = {}
    for path in sorted(claws_dir.glob(f'*/{CLAW_NAME}')):
        name = path.parent.name
        if not NAME_RE.fullmatch(name):
            raise ConfigError(f'{path}: invalid claw name {name!r}')
        base = dict(defaults.get('claw', {}))
        own = _read(path)
        data = _merge(base, own)
        data['job'] = _merge_jobs(base.get('job', []), own.get('job', []))
        own_names = {
            job.get('name')
            for job in own.get('job', [])
            if isinstance(job, dict)
        }
        shared = {
            job.get('name')
            for job in base.get('job', [])
            if isinstance(job, dict) and job.get('name') not in own_names
        }
        claws[name] = _claw(name, path.parent, data, shared)
    return Config(gateway=_gateway(defaults.get('gateway', {})), claws=claws)
