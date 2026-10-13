"""
A claw's OS sandbox: the sandbox-runtime config pi/claw-sandbox/launch.mts
runs the claw's pi in, built from its policy plus what pi needs to run.

pi and everything it starts share the one sandbox; macOS cannot nest them.
Reads are denied across $HOME (and per-user temp, and the claw state root),
then re-allowed for the policy's read_paths and pi's own needs; deny_read
then applies within those.
"""

import functools
import pathlib
import shutil
import subprocess
import sys
from collections.abc import Iterable

from .. import paths
from .. import state
from . import config

# Run by pi or the claw extensions (bash may need more, via read_paths).
# Each is readable through its link on PATH and the installation it resolves
# into.
RUNTIME_TOOLS = ('pi', 'node', 'rg', 'fd', 'gh', 'git', 'task', 'bakery')
# Read by every macOS process for locale settings; denying them only floods
# the violation log.
GLOBAL_PREFERENCES = (
    'Library/Preferences/.GlobalPreferences.plist',
    'Library/Preferences/.GlobalPreferences_m.plist',
    'Library/Preferences/ByHost/.GlobalPreferences.*.plist',
)
# pi's credential file in its profile. deny_read's defaults hide every
# claw's from the others; re-allowing a claw's own (literally, so it also
# beats the glob) lets its pi read it.
PI_CREDENTIALS = ('auth.json',)
# What pi writes in its profile (proper-lockfile locks are dirs beside their
# file); the rest of the profile, like the repo, is read-only. A new file pi
# needs shows up as a blocked write in the violation logs baker reviews.
PI_PROFILE_WRITES = (
    'auth.json', 'auth.json.lock',
    'models-store.json', 'models-store.json.lock',
    'settings.json', 'settings.json.lock',
)  # fmt: skip
# sandbox-runtime's default $TMPDIR, shared by every sandbox on the machine;
# claws get their own instead.
SHARED_TMP = ('/tmp/claude', '/private/tmp/claude')


def launcher() -> pathlib.Path:
    return paths.repo() / 'pi' / 'claw-sandbox' / 'launch.mts'


def home(claw: config.Claw) -> pathlib.Path:
    """The claw's XDG config, cache, and data homes live here."""
    return claw.state_dir / 'home'


def tmp(claw: config.Claw) -> pathlib.Path:
    """Each pi child's $TMPDIR is created (and removed) in here."""
    return claw.state_dir / 'tmp'


def sockets(claw: config.Claw) -> pathlib.Path:
    """pi/extensions/bakery.ts's control sockets, apart from other claws'."""
    return state.root() / '_gateway' / 'bakery' / claw.name


def git_config(claw: config.Claw) -> pathlib.Path:
    """
    The claw's global git config ($GIT_CONFIG_GLOBAL), so git neither reads
    nor fails on an unreadable ~/.gitconfig.
    """
    return home(claw) / 'config' / 'git' / 'config'


def prepare(claw: config.Claw) -> None:
    """
    What a claw's pi child needs before it starts: its dirs, and its git
    config. The config is rewritten every time, so the claw's own edits
    don't outlive the child.
    """
    for path in (claw.cwd, sockets(claw)):
        path.mkdir(parents=True, exist_ok=True)
    path = git_config(claw)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '# Written by the gateway when the claw starts.\n'
        '[user]\n'
        f'\tname = {claw.git.user_name}\n'
        f'\temail = {claw.git.user_email}\n'
        '[commit]\n'
        '\tgpgsign = false\n'
        # The sandbox refuses writes to any .git/hooks, so clones get none.
        '[init]\n'
        '\ttemplateDir =\n'
        # gh answers with $GH_TOKEN, the claws' GitHub user's token; there
        # is nothing to store (or erase) it in.
        '[credential]\n'
        '\thelper =\n'
        '[credential "https://github.com"]\n'
        '\thelper = "!f() { test \\"$1\\" = get &&'
        ' gh auth git-credential get; }; f"\n'
    )


def violations(claw: config.Claw, session_id: str) -> pathlib.Path:
    """What the sandbox blocked, out of the claw's reach (baker reads it)."""
    return (
        state.root() / '_gateway' / 'violations' / claw.name
    ) / f'{session_id}.jsonl'


@functools.cache
def _user_temp() -> pathlib.Path | None:
    """MacOS's per-user temp and cache dirs (/var/folders/...)."""
    if sys.platform != 'darwin':
        return None
    temp = subprocess.run(
        ('getconf', 'DARWIN_USER_TEMP_DIR'),
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return pathlib.Path(temp).resolve().parent


def _real(paths_: Iterable[pathlib.Path | str]) -> list[str]:
    """
    Each path through its real parent dir (eg. via /private/tmp, not /tmp),
    and through its own target if it is a link: the sandbox matches real
    paths, but leaves the spelling of missing ones alone. Keeping the link
    itself lets eg. a tool's link on PATH be resolved.
    """
    found: list[str] = []
    for raw in paths_:
        path = pathlib.Path(raw)
        for spelling in (path.parent.resolve() / path.name, path.resolve()):
            if str(spelling) not in found:
                found.append(str(spelling))
    return found


def _installation(exe: pathlib.Path) -> list[pathlib.Path]:
    """
    The link on PATH, and what running it reads: its npm packages, or its
    venv and the venv's base interpreter, or else just itself (never the
    dir around a binary, eg. a cargo home, which holds its credentials).
    """
    real = exe.resolve()
    for parent in real.parents:
        if parent.name == 'node_modules':
            return [exe, parent]
    venv = real.parent.parent / 'pyvenv.cfg'
    if not venv.is_file():
        return [exe]
    found = [exe, venv.parent]
    for line in venv.read_text().splitlines():
        key, _, value = line.partition('=')
        if key.strip() == 'home':
            found.append(pathlib.Path(value.strip()).resolve().parent)
    return found


def _runtime_reads(path_env: str) -> list[pathlib.Path]:
    reads = [paths.repo()]
    for tool in RUNTIME_TOOLS:
        exe = shutil.which(tool, path=path_env)
        if exe is not None:
            reads.extend(_installation(pathlib.Path(exe)))
    return reads


def settings(claw: config.Claw, path_env: str) -> dict[str, object]:
    """The sandbox-runtime config for `claw`'s pi, run with PATH `path_env`."""
    policy = claw.policy
    root = state.root()
    user_home = pathlib.Path.home()
    denies: list[pathlib.Path | str] = [user_home, root, *SHARED_TMP]
    user_temp = _user_temp()
    if user_temp is not None:
        denies.append(user_temp)
    reads = [
        *policy.read_paths,
        *_runtime_reads(path_env),
        claw.profile,
        *(claw.profile / name for name in PI_CREDENTIALS),
        claw.state_dir,
        root / state.SHARED,
        sockets(claw),
        *(user_home / p for p in GLOBAL_PREFERENCES),
    ]
    writes = [
        *policy.write_paths,
        *(claw.profile / name for name in PI_PROFILE_WRITES),
        claw.state_dir,
        sockets(claw),
    ]
    return {
        'network': {
            # Every host is allowed by launch.mts's callback; a network
            # config at all is what keeps the proxy and the unix socket
            # allowlist (without one, macOS gets a blanket allow network*).
            'allowedDomains': [],
            'deniedDomains': [],
            'allowUnixSockets': _real([sockets(claw)]),
            # Loopback directly, not only through the proxy: Go never
            # proxies localhost.
            'allowLocalBinding': True,
        },
        # Only lets Go programs (gh, ketch) verify TLS certificates through
        # the system's trust service; the Keychain, where my own gh token
        # lives, stays unreachable. Its documented risk, exfiltration through
        # trustd, adds nothing to a sandbox that reaches any public host.
        'enableWeakerNetworkIsolation': True,
        'filesystem': {
            'denyRead': [*_real(denies), *policy.deny_read],
            'allowRead': _real(reads),
            'allowWrite': _real(writes),
            'denyWrite': [],
            # Claws clone into their own (writable) state; nothing of mine is
            # writable, so this only lets them make and configure clones.
            # .git/hooks stays unwritable.
            'allowGitConfig': True,
        },
    }
