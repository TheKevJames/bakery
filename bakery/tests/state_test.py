import pathlib
import subprocess

import pytest

from bakery import state


def git(root: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ('git', '-C', str(root), *args),
        check=True,
        capture_output=True,
        text=True,
    ).stdout


@pytest.fixture(name='root', scope='function')
def fixture_root(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    # A signing-enabled global config with no identity: init must need
    # neither a signer nor a configured user.
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'gitconfig'))
    git(tmp_path, 'config', '--global', 'commit.gpgsign', 'true')
    return tmp_path / 'claws'


def test_init_is_idempotent_and_tracks_only_memory(root: pathlib.Path) -> None:
    state.init()
    state.user_file().write_text('# edited\n')
    for ignored in ('_gateway/runs.db', 'scout/sessions/a.jsonl'):
        (root / ignored).parent.mkdir(parents=True)
        (root / ignored).write_text('x')
    (root / 'scout' / 'MEMORY.md').write_text('remember\n')

    state.init()

    assert state.user_file().read_text() == '# edited\n'
    tracked = git(root, 'ls-files').split()
    assert sorted(tracked) == [
        '.gitignore',
        '_shared/USER.md',
        'scout/MEMORY.md',
    ]
    assert not git(root, 'status', '--porcelain').strip()
    assert len(git(root, 'log', '--oneline').splitlines()) == 2
