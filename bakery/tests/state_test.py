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


def test_init_is_idempotent_and_commits_only_its_own_files(
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
    assert sorted(tracked) == ['.gitignore', '_shared/OPERATOR.md']
    # The operator's edit is theirs to commit; memory is its claw's run's.
    status = commands.git(claws_state, 'status', '--porcelain').splitlines()
    assert sorted(status) == [' M _shared/OPERATOR.md', '?? scout/']
    assert len(commands.git(claws_state, 'log', '--oneline').splitlines()) == 1


def test_commit_records_only_the_given_paths(
    claws_state: pathlib.Path,
) -> None:
    state.init()
    for name in ('scout', 'scout-old', 'triage'):
        (claws_state / name).mkdir()
        (claws_state / name / 'MEMORY.md').write_text(f'{name}\n')
    state.operator_file().write_text('# edited\n')
    # Staged by someone else, eg. a commit that failed after its add.
    commands.git(claws_state, 'add', 'triage')

    assert state.commit('scout/dream-1: settled', 'scout')
    assert not state.commit('scout/dream-2: settled', 'scout')

    shown = commands.git(claws_state, 'show', '--name-only', '--format=%s')
    assert shown.split() == ['scout/dream-1:', 'settled', 'scout/MEMORY.md']
    status = commands.git(claws_state, 'status', '--porcelain').splitlines()
    assert sorted(status) == [
        ' M _shared/OPERATOR.md',
        '?? scout-old/',
        'A  triage/MEMORY.md',
    ]


def test_commit_needs_a_path(claws_state: pathlib.Path) -> None:
    del claws_state
    state.init()
    with pytest.raises(ValueError, match='at least one path'):
        state.commit('everything')
