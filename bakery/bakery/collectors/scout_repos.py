"""
Scout's repos: every one the claws' GitHub user can reach (inviting it adds a
repo), with per-repo overrides from the claw's `scout.toml`.
"""

import dataclasses
import operator
import pathlib
import tomllib
from typing import TYPE_CHECKING
from typing import Any

from .. import github
from .. import secrets
from ..gateway import toml

if TYPE_CHECKING:
    from ..gateway import config

SETTINGS_NAME = 'scout.toml'


@dataclasses.dataclass(frozen=True)
class Repo:
    name: str  # owner/name
    path: pathlib.Path
    exclude: tuple[str, ...]
    remote: str


@dataclasses.dataclass(frozen=True)
class Override:
    path: pathlib.Path | None
    exclude: tuple[str, ...]
    remote: str | None


@dataclasses.dataclass(frozen=True)
class Settings:
    # Where local checkouts live, as <checkouts>/<repo>, unless overridden.
    checkouts: pathlib.Path
    # By owner/name.
    overrides: dict[str, Override]
    max_candidates: int
    opt_out_label: str
    api: str


def load_settings(profile: pathlib.Path) -> Settings:
    path = profile / SETTINGS_NAME
    try:
        table = toml.Table(tomllib.loads(path.read_text()), SETTINGS_NAME)
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise toml.ConfigError(f'{path}: {e}') from None
    raw_repos = table.data.get('repo', [])
    table.used.add('repo')
    if not isinstance(raw_repos, list):
        raise toml.ConfigError(f'{SETTINGS_NAME}: repo must be a table array')
    overrides = {}
    for i, raw in enumerate(raw_repos):
        entry = toml.Table(raw, f'{SETTINGS_NAME}.repo[{i}]')
        overrides[entry.string('name')] = Override(
            path=toml.expand(entry.string('path'), profile)
            if entry.optional('path')
            else None,
            exclude=entry.strings('exclude')
            if entry.optional('exclude')
            else (),
            remote=entry.string('remote')
            if entry.optional('remote')
            else None,
        )
        entry.done()
    settings = Settings(
        checkouts=toml.expand(table.string('checkouts'), profile),
        overrides=overrides,
        max_candidates=table.count('max_candidates'),
        opt_out_label=table.string('opt_out_label'),
        api=table.string('api')
        if table.optional('api')
        else 'https://api.github.com',
    )
    table.done()
    return settings


def _accessible(gh: github.GitHub) -> list[dict[str, Any]]:
    """The claws' GitHub user's repos, by its collaborator access."""
    found = gh.get_all('user/repos', {'affiliation': 'collaborator'})
    return [meta for meta in found if not meta['archived']]


def repos(gh: github.GitHub, settings: Settings) -> list[Repo]:
    found = []
    for meta in sorted(_accessible(gh), key=operator.itemgetter('full_name')):
        name = meta['full_name']
        override = settings.overrides.get(name, Override(None, (), None))
        found.append(
            Repo(
                name=name,
                path=override.path or settings.checkouts / meta['name'],
                exclude=override.exclude,
                remote=override.remote or f'https://github.com/{name}.git',
            )
        )
    return found


def check(claw: 'config.Claw') -> list[str]:
    """Overrides for repos the claws cannot reach (eg. no longer invited)."""
    settings = load_settings(claw.profile)
    gh = github.GitHub(settings.api, secrets.get(claw.secrets['GH_TOKEN']))
    try:
        reachable = {meta['full_name'] for meta in _accessible(gh)}
    except github.GitHubError as e:
        return [f'{claw.name}: listing the repos failed: {e}']
    return [
        f'{claw.name}: {SETTINGS_NAME} overrides {name}, which the claws'
        " cannot reach; it's ignored"
        for name in sorted(set(settings.overrides) - reachable)
    ]
