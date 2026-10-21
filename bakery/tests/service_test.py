import argparse
import logging
import pathlib
import re
import shutil

import pytest

from bakery import service
from bakery.gateway import cli as gateway_cli
from bakery.gateway import main
from testing import gateway as testing_gateway

# A job naming a collector this bakery lacks: what a checkout newer than the
# installed bakery looks like to it.
UNKNOWN_COLLECTOR = """
    [[job]]
    name = "newer"
    collector = "from-the-future"
    prompt = "go"
"""
REJECTED = 'scout.job[2].collector: must be one of'


@pytest.fixture(name='launchctl', scope='function')
def fixture_launchctl(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> list[tuple[str, ...]]:
    """The launchctl calls made, with the plist kept under tmp_path."""
    calls: list[tuple[str, ...]] = []

    def launchctl(*args: str, check: bool = True) -> None:
        _ = check
        calls.append(args)

    monkeypatch.setenv('HOME', str(tmp_path / 'home'))
    monkeypatch.setattr(service, '_launchctl', launchctl)
    monkeypatch.setattr(shutil, 'which', lambda _: '/bin/bakery')
    return calls


def make_repo(
    root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    root.mkdir()
    testing_gateway.make_claws(root, {'scout': ''})
    monkeypatch.setenv('BAKERY_REPO', str(root))
    return root


def break_config(repo: pathlib.Path) -> None:
    with (repo / 'claws' / 'scout' / 'claw.toml').open('a') as f:
        f.write(UNKNOWN_COLLECTOR)


def test_install_and_restart_with_loadable_config(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launchctl: list[tuple[str, ...]],
) -> None:
    make_repo(tmp_path / 'repo', monkeypatch)

    path = service.install()
    service.restart()

    assert path.exists()
    assert [call[0] for call in launchctl] == [
        'bootout',
        'bootstrap',
        'kickstart',
    ]


def test_install_refuses_config_it_cannot_load(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launchctl: list[tuple[str, ...]],
) -> None:
    break_config(make_repo(tmp_path / 'repo', monkeypatch))

    with pytest.raises(service.ServiceError, match=re.escape(REJECTED)):
        service.install()

    # The running agent, if any, is left alone.
    assert not launchctl
    assert not service.plist_path().exists()


def test_restart_refuses_config_it_cannot_load(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launchctl: list[tuple[str, ...]],
) -> None:
    repo = make_repo(tmp_path / 'repo', monkeypatch)
    service.install()
    launchctl.clear()
    break_config(repo)
    # The agent runs from the checkout its plist records, not this shell's.
    make_repo(tmp_path / 'other', monkeypatch)

    with pytest.raises(SystemExit) as raised:
        gateway_cli.do_service(argparse.Namespace(service_command='restart'))

    message = str(raised.value.code)
    assert str(repo / 'claws') in message
    assert REJECTED in message
    # The checkout holds other code than this bakery's: say to upgrade.
    assert 'may just be out of date' in message
    assert f'uv tool install --force {repo / "bakery"}' in message
    assert not launchctl


def test_refusal_blames_the_config_when_the_code_is_the_checkouts(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    launchctl: list[tuple[str, ...]],
) -> None:
    repo = make_repo(tmp_path / 'repo', monkeypatch)
    shutil.copytree(
        pathlib.Path(service.__file__).parent,
        repo / 'bakery' / 'bakery',
        ignore=shutil.ignore_patterns('__pycache__'),
    )
    break_config(repo)

    with pytest.raises(service.ServiceError) as raised:
        service.restart()

    message = str(raised.value)
    assert REJECTED in message
    assert 'the config itself is wrong' in message
    assert 'uv tool install' not in message
    assert not launchctl


def test_run_logs_why_it_cannot_start(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    break_config(make_repo(tmp_path / 'repo', monkeypatch))
    # pytest owns the root logger; caplog stands in for gateway.log.
    monkeypatch.setattr(main, 'configure_logging', lambda: None)

    with (
        caplog.at_level(logging.ERROR, gateway_cli.log.name),
        pytest.raises(SystemExit, match=re.escape(REJECTED)),
    ):
        gateway_cli.do_run(argparse.Namespace(claws_dir=None))

    assert 'gateway cannot start' in caplog.text
    assert REJECTED in caplog.text
