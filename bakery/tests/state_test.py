import pathlib

import pytest

from bakery import state
from testing import commands


@pytest.fixture(name='claws_state', scope='function')
def fixture_claws_state(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """The claw state root, not yet created."""
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))
    # A signing-enabled global config with no identity: init must need
    # neither a signer nor a configured user.
    monkeypatch.setenv('GIT_CONFIG_GLOBAL', str(tmp_path / 'gitconfig'))
    commands.git(tmp_path, 'config', '--global', 'commit.gpgsign', 'true')
    return tmp_path / 'claws'


def test_init_is_idempotent_and_tracks_only_memory(
    claws_state: pathlib.Path,
) -> None:
    state.init()
    state.operator_file().write_text('# edited\n')
    for ignored in ('_gateway/runs.db', 'scout/sessions/a.jsonl'):
        (claws_state / ignored).parent.mkdir(parents=True)
        (claws_state / ignored).write_text('x')
    (claws_state / 'scout' / 'MEMORY.md').write_text('remember\n')

    state.init()

    assert state.operator_file().read_text() == '# edited\n'
    tracked = commands.git(claws_state, 'ls-files').split()
    assert sorted(tracked) == [
        '.gitignore',
        '_shared/OPERATOR.md',
        'scout/MEMORY.md',
    ]
    assert not commands.git(claws_state, 'status', '--porcelain').strip()
    assert len(commands.git(claws_state, 'log', '--oneline').splitlines()) == 2
