"""`bakery gateway run`: the long-lived daemon (normally run by launchd)."""

import asyncio
import logging
import logging.handlers
import pathlib
import signal
import sys

from .. import secrets
from .. import state
from ..chat import discord_transport
from ..chat import relay
from . import config
from . import control
from . import core

TOKEN = 'DISCORD_BOT_TOKEN'
LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUPS = 3

log = logging.getLogger(__name__)


def configure_logging() -> None:
    path = state.root() / '_gateway' / 'gateway.log'
    path.parent.mkdir(parents=True, exist_ok=True)
    handlers: list[logging.Handler] = [
        logging.handlers.RotatingFileHandler(
            path, maxBytes=LOG_BYTES, backupCount=LOG_BACKUPS
        )
    ]
    if sys.stderr.isatty():
        handlers.append(logging.StreamHandler())
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(name)s: %(message)s',
        handlers=handlers,
    )


def transport(
    claws_dir: pathlib.Path, loaded: config.Config
) -> discord_transport.DiscordTransport:
    settings = loaded.gateway.discord
    if settings is None:
        raise config.ConfigError(
            'gateway.discord is not configured; run bin/discord-setup'
        )
    avatars = {
        name: claws_dir / name / 'avatar.png'
        for name in loaded.claws
        if (claws_dir / name / 'avatar.png').exists()
    }
    return discord_transport.DiscordTransport(
        secrets.get(TOKEN), settings.guild_id, settings.owner_id, avatars
    )


def _enabled(loaded: config.Config) -> list[str]:
    return [claw.name for claw in loaded.claws.values() if claw.enabled]


async def run(claws_dir: pathlib.Path) -> None:
    state.init()
    loaded = config.load(claws_dir)
    chat = transport(claws_dir, loaded)
    assert loaded.gateway.discord is not None
    bot = relay.Relay(chat, loaded.gateway.discord.owner_id)
    gateway = core.Gateway(claws_dir, bot)
    bot.bind(gateway)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)

    def reload() -> None:
        try:
            gateway.reload()
            log.info('reloaded configuration')
        except config.ConfigError:
            log.exception('reload rejected')

    loop.add_signal_handler(signal.SIGHUP, reload)

    await chat.start(bot, _enabled(loaded))
    await gateway.start()
    stop_control = await control.serve(gateway)
    log.info('gateway running')
    try:
        await stop.wait()
    finally:
        log.info('gateway stopping')
        await stop_control()
        await gateway.stop()
        await chat.close()


async def check(claws_dir: pathlib.Path) -> None:
    """Connect, ensure the Discord layout exists, say hello, disconnect."""
    loaded = config.load(claws_dir)
    chat = transport(claws_dir, loaded)
    assert loaded.gateway.discord is not None
    bot = relay.Relay(chat, loaded.gateway.discord.owner_id)
    claws = _enabled(loaded)
    await chat.start(bot, claws)
    try:
        await bot.notice(
            f'bakery check ✓ (claws: {", ".join(claws) or "none"})'
        )
    finally:
        await chat.close()
