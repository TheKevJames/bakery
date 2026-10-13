"""Helpers for running a real Gateway (with real pi) in tests."""

import asyncio
import collections
import dataclasses
import json
import logging
import pathlib
import re
import textwrap
import threading
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping

from bakery.chat import relay
from bakery.gateway import channel
from bakery.gateway import control
from bakery.gateway import core

from . import harness

TESTING = pathlib.Path(__file__).parent
RUN_TIMEOUT = 60.0

Dialog = Callable[
    [channel.RunInfo, dict[str, object]], Awaitable[dict[str, object]]
]


async def _deny(
    info: channel.RunInfo, request: dict[str, object]
) -> dict[str, object]:
    del info, request
    return {'cancelled': True}


@dataclasses.dataclass
class FakeChannel:
    """Records what the gateway reports; `dialogs` answers dialogs."""

    dialogs: Dialog = _deny
    finished: list[channel.RunInfo] = dataclasses.field(default_factory=list)
    asked: list[dict[str, object]] = dataclasses.field(default_factory=list)
    notices: list[str] = dataclasses.field(default_factory=list)
    _done: collections.defaultdict[str, asyncio.Queue[channel.RunInfo]] = (
        dataclasses.field(
            default_factory=lambda: collections.defaultdict(asyncio.Queue)
        )
    )
    _changed: asyncio.Event = dataclasses.field(default_factory=asyncio.Event)

    async def notice(self, text: str) -> None:
        self.notices.append(text)
        self._changed.set()

    async def run_started(self, info: channel.RunInfo) -> None:
        del info

    async def run_progress(self, info: channel.RunInfo) -> None:
        del info

    async def run_finished(self, info: channel.RunInfo) -> None:
        self.finished.append(info)
        self._done[info.work_key].put_nowait(info)
        self._changed.set()

    async def dialog(
        self, info: channel.RunInfo, request: dict[str, object]
    ) -> dict[str, object]:
        self.asked.append(request)
        return await self.dialogs(info, request)

    async def wait(self, work_key: str) -> channel.RunInfo:
        """The next finished run of `work_key`."""
        return await asyncio.wait_for(self._done[work_key].get(), RUN_TIMEOUT)

    async def until(self, predicate: Callable[[], object]) -> None:
        """Wait until `predicate()` is truthy after something is reported."""
        async with asyncio.timeout(RUN_TIMEOUT):
            while not predicate():
                self._changed.clear()
                await self._changed.wait()


class LogWatch(logging.Handler):
    """Collects log messages, to wait on what only gateway.log shows."""

    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []
        self._logged = threading.Condition()

    def emit(self, record: logging.LogRecord) -> None:
        with self._logged:
            self.messages.append(record.getMessage())
            self._logged.notify_all()

    def wait_for(self, text: str) -> None:
        """Block until a message containing `text` is logged."""
        with self._logged:
            logged = self._logged.wait_for(
                lambda: any(text in m for m in self.messages), RUN_TIMEOUT
            )
        assert logged, text


async def ctl(record: Mapping[str, object]) -> object:
    """A control request, as the CLI sends it."""
    return await asyncio.to_thread(control.send, record)


async def claw_status(claw: str) -> dict[str, object]:
    """`claw`'s entry in the control socket's status."""
    status = await ctl({'type': 'status'})
    assert isinstance(status, dict)
    claws = status['claws']
    assert isinstance(claws, dict)
    entry: dict[str, object] = claws[claw]
    return entry


def make_claws(
    root: pathlib.Path,
    claws: dict[str, str],
    *,
    extensions: dict[str, list[str]] | None = None,
    gateway: dict[str, object] | None = None,
    settings: dict[str, dict[str, object]] | None = None,
) -> pathlib.Path:
    """
    A claws dir: the repo's real defaults.toml, plus one profile per claw.

    `claws` maps names to extra claw.toml text; every claw uses the fake
    model and loads the claw prompts. `gateway` overrides values in
    defaults.toml's [gateway] table; `settings` adds to a claw's settings.json.
    """
    claws_dir = root / 'claws'
    claws_dir.mkdir()
    defaults = (harness.REPO / 'claws' / 'defaults.toml').read_text()
    for key, value in (gateway or {}).items():
        # [gateway] comes first, so its key is the first match.
        defaults, count = re.subn(
            rf'^{key} = \S+',
            f'{key} = {value}',
            defaults,
            count=1,
            flags=re.MULTILINE,
        )
        assert count == 1, key
    (claws_dir / 'defaults.toml').write_text(defaults)
    for name, extra in claws.items():
        _make_profile(
            claws_dir / name,
            extra,
            (extensions or {}).get(name, []),
            (settings or {}).get(name, {}),
        )
    return claws_dir


def run_gateway(
    claws_dir: pathlib.Path,
    fake: channel.Channel,
    scenario: Callable[[core.Gateway], Awaitable[None]],
) -> None:
    """Start a gateway with its control socket, run `scenario`, stop it."""

    async def main() -> None:
        gateway = core.Gateway(claws_dir, fake)
        if isinstance(fake, relay.Relay):
            fake.bind(gateway)
        await gateway.start()
        stop_control = await control.serve(gateway)
        try:
            await scenario(gateway)
        finally:
            await stop_control()
            await gateway.stop()

    asyncio.run(main())


def _make_profile(
    profile: pathlib.Path,
    extra: str,
    extensions: list[str],
    settings: dict[str, object],
) -> None:
    profile.mkdir()
    # Test-only extensions live here; others are repo paths.
    paths = [harness.FAKE_PROVIDER] + [
        TESTING / ext if (TESTING / ext).exists() else harness.REPO / ext
        for ext in extensions
    ]
    profile_settings: dict[str, object] = {
        'extensions': [str(p) for p in paths],
        'prompts': [str(harness.REPO / 'pi' / 'claw-prompts')],
    } | settings
    (profile / 'settings.json').write_text(json.dumps(profile_settings))
    extra = textwrap.dedent(extra)
    toml = 'model = "fake/echo"\nthinking = "off"\n'
    if not re.search(r'^secrets\s*=', extra, re.MULTILINE):
        toml += 'secrets = ["BAKERY_FAKE_LLM_URL"]\n'
    # The default names GH_TOKEN, which test claws' secrets lack.
    if not re.search(r'^bash_secrets\s*=', extra, re.MULTILINE):
        toml += 'bash_secrets = []\n'
    (profile / 'claw.toml').write_text(toml + extra)
