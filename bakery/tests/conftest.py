import http.server
import os
import pathlib
import shutil
import tempfile
import threading
from collections.abc import Callable
from collections.abc import Iterator

import pytest

from bakery import secrets
from testing import harness


@pytest.fixture(name='fake_llm', scope='function')
def fixture_fake_llm() -> Iterator[harness.FakeLLM]:
    llm = harness.FakeLLM(url='', requests=[])
    server = http.server.ThreadingHTTPServer(
        ('127.0.0.1', 0), harness.handler(llm)
    )
    llm.url = f'http://127.0.0.1:{server.server_port}/v1'
    thread = threading.Thread(
        target=server.serve_forever, args=(0.01,), daemon=True
    )
    thread.start()
    yield llm
    server.shutdown()


@pytest.fixture(name='run_pi', scope='function')
def fixture_run_pi(
    tmp_path: pathlib.Path, fake_llm: harness.FakeLLM
) -> Iterator[Callable[[pathlib.Path], harness.Pi]]:
    state = tmp_path / 'state'
    cwd = tmp_path / 'cwd'
    cwd.mkdir()
    # macOS caps unix socket paths at 104 bytes; pytest's tmp_path is longer.
    sockets = pathlib.Path(tempfile.mkdtemp(prefix='bk', dir='/tmp'))

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
            'PI_CODING_AGENT_BAKERY_DIR': str(sockets),
            'PI_CODING_AGENT_SESSION_DIR': str(state / 'sessions'),
            'XDG_STATE_HOME': str(state),
        }
        return harness.Pi(profile, env, cwd)

    yield make
    shutil.rmtree(sockets)


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
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[pathlib.Path]:
    """A gateway's world: state dir, fake model, and the repo's extensions."""
    # Short, because the state dir holds unix sockets (104-byte path cap).
    root = pathlib.Path(tempfile.mkdtemp(prefix='bk', dir='/tmp'))
    monkeypatch.setenv('XDG_STATE_HOME', str(root / 'state'))
    monkeypatch.setenv('TASK_FOLDER', str(root / 'tasks'))
    keychain['BAKERY_FAKE_LLM_URL'] = fake_llm.url
    monkeypatch.setenv('BAKERY_REPO', str(harness.REPO))
    yield root
    shutil.rmtree(root)
