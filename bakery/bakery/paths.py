"""Resolve base directories from environment variables with home fallbacks."""

import os
import pathlib


class PathError(Exception):
    pass


def repo() -> pathlib.Path:
    """
    The bakery checkout: $BAKERY_REPO, else inferred from the interactive
    profile ($PI_CODING_AGENT_DIR is the checkout's interactive/ dir).
    """
    override = os.getenv('BAKERY_REPO', '').strip()
    if override:
        return pathlib.Path(override).expanduser().resolve()
    agent_dir = os.getenv('PI_CODING_AGENT_DIR', '').strip()
    if agent_dir and pathlib.Path(agent_dir).name == 'interactive':
        return pathlib.Path(agent_dir).expanduser().resolve().parent
    raise PathError('cannot find the bakery checkout; set BAKERY_REPO')


def resolve_base(
    env_var: str, fallback: pathlib.Path, *subpath: str
) -> pathlib.Path:
    """Env var (if set) else `fallback` as the base, joined with `subpath`."""
    value = os.getenv(env_var, '').strip()
    base = pathlib.Path(value) if value else fallback
    return base.joinpath(*subpath).resolve()
