"""`bakery service`: run the gateway as a launchd user agent."""

import os
import pathlib
import plistlib
import shutil
import subprocess

from . import paths
from . import state
from .gateway import config

LABEL = 'in.thekev.bakery'
# Recorded at install time so the agent finds pi, node, gh, and the task
# folder as the operator's shell does; launchd otherwise starts with a bare
# environment.
RECORDED_ENV = ('PATH', 'HOME', 'LANG', 'TASK_FOLDER')
RECORDED_ENV_PREFIXES = ('XDG_',)


class ServiceError(Exception):
    pass


def plist_path() -> pathlib.Path:
    return pathlib.Path.home() / 'Library' / 'LaunchAgents' / f'{LABEL}.plist'


def _domain() -> str:
    return f'gui/{os.getuid()}'


def render() -> bytes:
    executable = shutil.which('bakery')
    if executable is None:
        raise ServiceError('bakery is not on PATH; install it with uv tool')
    env = {
        name: value
        for name, value in os.environ.items()
        if name in RECORDED_ENV or name.startswith(RECORDED_ENV_PREFIXES)
    }
    env['BAKERY_REPO'] = str(paths.repo())
    log = state.root() / '_gateway' / 'launchd.log'
    return plistlib.dumps(
        {
            'Label': LABEL,
            'ProgramArguments': [executable, 'gateway', 'run'],
            'EnvironmentVariables': env,
            'RunAtLoad': True,
            'KeepAlive': True,
            # Back off if the gateway keeps crashing (eg. a bad token).
            'ThrottleInterval': 30,
            'StandardOutPath': str(log),
            'StandardErrorPath': str(log),
        }
    )


def _launchctl(*args: str, check: bool = True) -> None:
    result = subprocess.run(
        ('launchctl', *args), capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise ServiceError(
            f'launchctl {" ".join(args)} failed: {result.stderr.strip()}'
        )


def _recorded_repo() -> pathlib.Path:
    """The checkout the installed agent runs from, else this shell's."""
    try:
        recorded = plistlib.loads(plist_path().read_bytes())
        return pathlib.Path(recorded['EnvironmentVariables']['BAKERY_REPO'])
    except (OSError, plistlib.InvalidFileException, KeyError, TypeError):
        return paths.repo()


def _sources(package: pathlib.Path) -> dict[pathlib.Path, bytes]:
    return {
        path.relative_to(package): path.read_bytes()
        for path in package.rglob('*')
        if path.is_file() and '__pycache__' not in path.parts
    }


def _code_differs(repo: pathlib.Path) -> bool:
    """Whether this bakery's code is not the checkout's (a stale install)."""
    running = pathlib.Path(__file__).resolve().parent
    checkout = (repo / 'bakery' / 'bakery').resolve()
    return running != checkout and _sources(running) != _sources(checkout)


def check_config(repo: pathlib.Path) -> None:
    """
    Load the checkout's claws config with this (the installed) bakery, as the
    agent will on start: if it cannot, launchd respawns it in a throttled loop
    and `kickstart -k` hangs, with every claw down.
    """
    claws = repo / 'claws'
    try:
        config.load(claws)
    except config.ConfigError as e:
        running = pathlib.Path(__file__).resolve().parent
        if _code_differs(repo):
            remedy = (
                f'this bakery ({running}) is not the code in {repo}, so it '
                'may just be out of date: reinstall it with `uv tool install '
                f'--force {repo / "bakery"}`, then retry; if the error stays, '
                'fix the config'
            )
        else:
            remedy = (
                f'this bakery ({running}) is the code in {repo}, so the '
                'config itself is wrong: fix it, then retry'
            )
        raise ServiceError(
            f'refusing to (re)start the gateway: it cannot load {claws}:\n'
            f'  {e}\n{remedy}'
        ) from e


def install() -> pathlib.Path:
    check_config(paths.repo())
    path = plist_path()
    (state.root() / '_gateway').mkdir(parents=True, exist_ok=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render())
    _launchctl('bootout', f'{_domain()}/{LABEL}', check=False)
    _launchctl('bootstrap', _domain(), str(path))
    return path


def uninstall() -> None:
    _launchctl('bootout', f'{_domain()}/{LABEL}', check=False)
    plist_path().unlink(missing_ok=True)


def restart() -> None:
    check_config(_recorded_repo())
    _launchctl('kickstart', '-k', f'{_domain()}/{LABEL}')
