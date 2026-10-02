"""
Gateway and claw configuration.

`claws/defaults.toml` holds a `[gateway]` table and a `[claw]` table of
defaults; each `claws/<name>/claw.toml` overrides any of those defaults and
adds its jobs. Loading is strict: unknown keys and bad values raise
ConfigError, so a typo can never silently change a limit.
"""

import dataclasses
import datetime
import os
import pathlib
import re
import tomllib
import zoneinfo
from collections.abc import Mapping
from typing import Any

import croniter

from .. import state

DEFAULTS_NAME = 'defaults.toml'
CLAW_NAME = 'claw.toml'
NAME_RE = re.compile(r'[a-z][a-z0-9-]*')
ENV_NAME_RE = re.compile(r'[A-Z_][A-Z0-9_]*')
ASK_POLICIES = ('ask', 'assume', 'park')
TIMEOUT_POLICIES = ('assume', 'park')


class ConfigError(Exception):
    pass


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
    name: str
    cron: str
    prompt: str
    active_hours: ActiveHours | None
    silent_ok: bool


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
    secrets: tuple[str, ...]
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


class _Table:
    """Typed, strict access to one TOML table; reports unused keys."""

    def __init__(self, data: Mapping[str, Any], where: str) -> None:
        self.data = data
        self.where = where
        self.used: set[str] = set()

    def _get(self, key: str) -> object:
        self.used.add(key)
        if key not in self.data:
            raise ConfigError(f'{self.where}: missing {key}')
        return self.data[key]

    def _wrong_type(self, key: str) -> ConfigError:
        return ConfigError(f'{self.where}.{key}: wrong type')

    def string(self, key: str, choices: tuple[str, ...] = ()) -> str:
        value = self._get(key)
        if not isinstance(value, str):
            raise self._wrong_type(key)
        if choices and value not in choices:
            raise ConfigError(
                f'{self.where}.{key}: must be one of {", ".join(choices)}'
            )
        return value

    def boolean(self, key: str) -> bool:
        value = self._get(key)
        if not isinstance(value, bool):
            raise self._wrong_type(key)
        return value

    def positive(self, key: str) -> float:
        value = self._get(key)
        # bool is an int subclass; `true` is not a number here.
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self._wrong_type(key)
        if value <= 0:
            raise ConfigError(f'{self.where}.{key}: must be positive')
        return float(value)

    def count(self, key: str, *, minimum: int = 1) -> int:
        value = self._get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            raise self._wrong_type(key)
        if value < minimum:
            raise ConfigError(f'{self.where}.{key}: must be >= {minimum}')
        return value

    def names(self, key: str) -> tuple[str, ...]:
        """A list of environment-variable names."""
        value = self._get(key)
        if not isinstance(value, list) or not all(
            isinstance(x, str) and ENV_NAME_RE.fullmatch(x) for x in value
        ):
            raise ConfigError(f'{self.where}.{key}: must be a list of names')
        return tuple(str(x) for x in value)

    def table(self, key: str) -> '_Table':
        value = self._get(key)
        if not isinstance(value, dict):
            raise self._wrong_type(key)
        return _Table(value, f'{self.where}.{key}')

    def optional(self, key: str) -> bool:
        return key in self.data

    def done(self) -> None:
        unknown = set(self.data) - self.used
        if unknown:
            raise ConfigError(
                f'{self.where}: unknown keys {", ".join(sorted(unknown))}'
            )


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


def _expand(raw: str, base: pathlib.Path) -> pathlib.Path:
    def env(match: re.Match[str]) -> str:
        value = os.environ.get(match.group(1))
        if not value:
            raise ConfigError(f'${{{match.group(1)}}} is not set (in {raw})')
        return value

    expanded = re.sub(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}', env, raw)
    return (base / pathlib.Path(expanded).expanduser()).resolve()


def _active_hours(raw: str, where: str) -> ActiveHours:
    match = re.fullmatch(r'(\d\d:\d\d)-(\d\d:\d\d)', raw)
    if not match:
        raise ConfigError(f'{where}.active_hours: expected HH:MM-HH:MM')
    try:
        start, end = (datetime.time.fromisoformat(x) for x in match.groups())
    except ValueError as e:
        raise ConfigError(f'{where}.active_hours: {e}') from None
    return ActiveHours(start, end)


def _job(table: _Table, names: set[str]) -> Job:
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
    )
    table.done()
    return job


def _claw(name: str, profile: pathlib.Path, data: Mapping[str, Any]) -> Claw:
    table = _Table(data, name)
    limits = table.table('limits')
    concurrency = table.table('concurrency')
    ask = table.table('ask')
    jobs_raw = data.get('job', [])
    table.used.add('job')
    if not isinstance(jobs_raw, list):
        raise ConfigError(f'{name}.job: must be an array of tables')
    names: set[str] = set()
    jobs = tuple(
        _job(_Table(job, f'{name}.job[{i}]'), names)
        for i, job in enumerate(jobs_raw)
    )
    cwd = (
        _expand(table.string('cwd'), profile)
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
        secrets=table.names('secrets'),
        ask=Ask(
            policy=ask.string('policy', ASK_POLICIES),
            timeout_hours=ask.positive('timeout_hours'),
            on_timeout=ask.string('on_timeout', TIMEOUT_POLICIES),
        ),
        jobs=jobs,
    )
    for sub in (limits, concurrency, ask, table):
        sub.done()
    return claw


def _gateway(data: Mapping[str, Any]) -> Gateway:
    table = _Table(data, 'gateway')
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
        data = _merge(defaults.get('claw', {}), _read(path))
        claws[name] = _claw(name, path.parent, data)
    return Config(gateway=_gateway(defaults.get('gateway', {})), claws=claws)
