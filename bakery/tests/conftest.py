import logging
import os
import pathlib
import shutil
import tempfile
import threading
from collections.abc import Callable
from collections.abc import Iterator

import pytest

from bakery import secrets
from testing import gateway as testing_gateway
from testing import harness
from testing import servers


@pytest.fixture(name='fake_llm', scope='function')
def fixture_fake_llm() -> Iterator[harness.FakeLLM]:
    llm = harness.FakeLLM(url='', requests=[])
    server = servers.LocalServer(('127.0.0.1', 0), harness.handler(llm))
    llm.url = f'http://127.0.0.1:{server.server_port}/v1'
    thread = threading.Thread(
        target=server.serve_forever, args=(0.01,), daemon=True
    )
    thread.start()
    yield llm
    server.shutdown()


@pytest.fixture(name='short_tmp', scope='function')
def fixture_short_tmp() -> Iterator[pathlib.Path]:
    """
    A temp dir for state holding pi's registry sockets: macOS caps unix socket
    paths at 104 bytes, and pytest's tmp_path is longer.
    """
    path = pathlib.Path(tempfile.mkdtemp(prefix='bk', dir='/tmp'))
    yield path
    shutil.rmtree(path)


@pytest.fixture(name='run_pi', scope='function')
def fixture_run_pi(
    tmp_path: pathlib.Path, short_tmp: pathlib.Path, fake_llm: harness.FakeLLM
) -> Callable[[pathlib.Path], harness.Pi]:
    state = tmp_path / 'state'
    cwd = tmp_path / 'cwd'
    cwd.mkdir()

    def make(profile: pathlib.Path) -> harness.Pi:
        env = os.environ | {
            # Real profiles name real models; dummy keys make them resolve
            # (as on a configured machine) and ensure nothing reaches a
            # real provider with real credentials.
            'ANTHROPIC_API_KEY': 'fake',
            'GEMINI_API_KEY': 'fake',
            'OPENAI_API_KEY': 'fake',
            'BAKERY_FAKE_LLM_URL': fake_llm.url,
            'PI_CODING_AGENT_DIR': str(profile),
            'PI_CODING_AGENT_STATE_DIR': str(short_tmp),
            'PI_CODING_AGENT_SESSION_DIR': str(state / 'sessions'),
            'XDG_STATE_HOME': str(state),
        }
        return harness.Pi(profile, env, cwd)

    return make


@pytest.fixture(name='logs', scope='function')
def fixture_logs() -> Iterator[testing_gateway.LogWatch]:
    """What bakery logs, from INFO up."""
    logger = logging.getLogger('bakery')
    level = logger.level
    watch = testing_gateway.LogWatch()
    logger.addHandler(watch)
    logger.setLevel(logging.INFO)
    yield watch
    logger.removeHandler(watch)
    logger.setLevel(level)


@pytest.fixture(name='keychain', scope='function', autouse=True)
def fixture_keychain(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """The Keychain's items, by name; add to it to store a secret."""
    items: dict[str, str] = {}
    monkeypatch.setattr(secrets, 'keychain', items.get)
    return items


@pytest.fixture(name='task_folder', scope='function', autouse=True)
def fixture_task_folder(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """$TASK_FOLDER, which defaults.toml grants every claw; not created."""
    folder = tmp_path / 'tasks'
    monkeypatch.setenv('TASK_FOLDER', str(folder))
    return folder


@pytest.fixture(name='root', scope='function')
def fixture_root(
    fake_llm: harness.FakeLLM,
    keychain: dict[str, str],
    short_tmp: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> pathlib.Path:
    """A gateway's world: state dir, fake model, and the repo's extensions."""
    root = short_tmp
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    monkeypatch.setenv('TASK_FOLDER', str(root / 'tasks'))
    keychain['BAKERY_FAKE_LLM_URL'] = fake_llm.url
    monkeypatch.setenv('BAKERY_REPO', str(harness.REPO))
    return root
