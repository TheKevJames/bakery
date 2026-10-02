"""`bakery service`: run the gateway as a launchd user agent."""

import os
import pathlib
import plistlib
import shutil
import subprocess

from . import paths
from . import state

LABEL = 'in.thekev.bakery'
# Recorded at install time so the agent finds pi, node, gh, and the task
# folder as my shell does; launchd otherwise starts with a bare environment.
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
        raise ServiceError('bakery is not on PATH; install it with pipx')
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


def install() -> pathlib.Path:
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
    _launchctl('kickstart', '-k', f'{_domain()}/{LABEL}')
