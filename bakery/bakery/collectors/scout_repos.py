"""
Scout's repos: every one the claws' GitHub user can reach (inviting it adds a
repo), with per-repo exclude globs from the claw's `scout.toml`.
"""

import dataclasses
import operator
import pathlib
import re
import subprocess
import tomllib
from typing import TYPE_CHECKING
from typing import Any

from .. import github
from .. import secrets
from ..gateway import toml
from . import base

if TYPE_CHECKING:
    from ..gateway import config

SETTINGS_NAME = 'scout.toml'


@dataclasses.dataclass(frozen=True)
class Repo:
    name: str  # owner/name
    clone: pathlib.Path
    exclude: tuple[str, ...]
    remote: str


@dataclasses.dataclass(frozen=True)
class Settings:
    # Each repo's exclude globs, by owner/name.
    excludes: dict[str, tuple[str, ...]]
    # CI log lines (warnings and errors) matching any of these (searched)
    # are never candidates.
    ignore_ci_lines: tuple[re.Pattern[str], ...]
    max_candidates: int
    opt_out_label: str
    api: str
    # Repos are fetched from <git_url>/<owner>/<name>.git.
    git_url: str


def git(repo: Repo, *args: str, env: dict[str, str] | None = None) -> str:
    """Run git in the repo's clone; returns its stdout."""
    return subprocess.run(
        ('git', '-C', str(repo.clone), *args),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    ).stdout


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
    excludes = {}
    for i, raw in enumerate(raw_repos):
        entry = toml.Table(raw, f'{SETTINGS_NAME}.repo[{i}]')
        excludes[entry.string('name')] = entry.strings('exclude')
        entry.done()
    settings = Settings(
        excludes=excludes,
        ignore_ci_lines=_patterns(table, 'ignore_ci_lines')
        if table.optional('ignore_ci_lines')
        else (),
        max_candidates=table.count('max_candidates'),
        opt_out_label=table.string('opt_out_label'),
        api=table.string('api')
        if table.optional('api')
        else 'https://api.github.com',
        git_url=table.string('git_url')
        if table.optional('git_url')
        else 'https://github.com',
    )
    table.done()
    return settings


def _patterns(table: toml.Table, key: str) -> tuple[re.Pattern[str], ...]:
    patterns = []
    for raw in table.strings(key):
        try:
            patterns.append(re.compile(raw))
        except re.error as e:
            raise toml.ConfigError(
                f'{table.where}.{key}: {raw!r}: {e}'
            ) from None
    return tuple(patterns)


def _accessible(gh: github.GitHub) -> list[dict[str, Any]]:
    """The claws' GitHub user's repos, by its collaborator access."""
    found = gh.get_all('user/repos', {'affiliation': 'collaborator'})
    return [meta for meta in found if not meta['archived']]


def repos(
    gh: github.GitHub, settings: Settings, clones: pathlib.Path
) -> list[Repo]:
    """Every reachable repo; clones live at <clones>/<owner>/<name>.git."""
    found = []
    for meta in sorted(_accessible(gh), key=operator.itemgetter('full_name')):
        name = meta['full_name']
        found.append(
            Repo(
                name=name,
                clone=clones / f'{name}.git',
                exclude=settings.excludes.get(name, ()),
                remote=f'{settings.git_url}/{name}.git',
            )
        )
    return found


def check(claw: 'config.Claw') -> list[str]:
    """Overrides for repos the claws cannot reach (eg. no longer invited)."""
    settings = load_settings(claw.profile)
    gh = github.GitHub(settings.api, secrets.get(base.GITHUB_TOKEN))
    try:
        reachable = {meta['full_name'] for meta in _accessible(gh)}
    except github.GitHubError as e:
        return [f'{claw.name}: listing the repos failed: {e}']
    return [
        f'{claw.name}: {SETTINGS_NAME} overrides {name}, which the claws'
        " cannot reach; it's ignored"
        for name in sorted(set(settings.excludes) - reachable)
    ]
