"""
The Discord Transport, via discord.py.

Kept thin: no decisions are made here, so the relay's behaviour can be tested
against a fake transport. Layout (category, channels, webhooks) is created on
first connect if missing.

Claws post through one bot-owned webhook per channel, so each has its own
name and avatar. Because the bot owns them, their messages can carry
interactive components, whose clicks are delivered to the bot. A webhook
deleted while the gateway runs is found or created again on its next use.
"""

import asyncio
import contextlib
import io
import logging
import pathlib
import re
from collections.abc import Generator
from collections.abc import Sequence
from typing import Any

import discord
from discord import app_commands
from discord import ui

from . import transport

log = logging.getLogger(__name__)

CATEGORY = 'bakery'
CONTROL_CHANNEL = 'bakery'
# 7 days: units of work (eg. a build task's PR review) can idle for days.
ARCHIVE_MINUTES = 10080
# Discord's JSON error codes.
UNKNOWN_CHANNEL = 10003
UNKNOWN_MESSAGE = 10008
UNKNOWN_WEBHOOK = 10015


def channel_name(claw: str) -> str:
    return f'{CONTROL_CHANNEL}-{claw}'


@contextlib.contextmanager
def _gone() -> Generator[None]:
    """Translate a webhook's error for a deleted thread or message."""
    try:
        yield
    # By code, not status: posting to a deleted thread is a 400, not a 404.
    except discord.HTTPException as e:
        if e.code == UNKNOWN_CHANNEL:
            raise transport.ThreadGone(str(e)) from e
        if e.code == UNKNOWN_MESSAGE:
            raise transport.MessageGone(str(e)) from e
        raise


def _action(handler: transport.Handler) -> type[Any]:
    """A button class that dispatches to `handler`, even across restarts."""

    class Action(
        ui.DynamicItem[ui.Button[Any]], template=r'bakery:resume:\d+'
    ):
        def __init__(self, button: ui.Button[Any]) -> None:
            super().__init__(button)

        @classmethod
        async def from_custom_id(
            cls,
            interaction: discord.Interaction,
            item: ui.Item[Any],
            match: re.Match[str],
            /,
        ) -> 'Action':
            del interaction, match
            assert isinstance(item, ui.Button)
            return cls(item)

        async def callback(self, interaction: discord.Interaction) -> None:
            reply = await handler.on_component(
                interaction.user.id, str(self.item.custom_id)
            )
            await interaction.response.send_message(reply, ephemeral=True)

    return Action


class DiscordTransport:
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        token: str,
        guild_id: int,
        operator_id: int,
        avatars: dict[str, pathlib.Path],
    ) -> None:
        self.token = token
        self.guild_id = guild_id
        self.operator_id = operator_id
        self.avatars = avatars
        intents = discord.Intents.default()
        intents.message_content = True
        self.client = discord.Client(
            intents=intents, allowed_mentions=self._mentions()
        )
        self.tree = app_commands.CommandTree(self.client)
        self.control: discord.TextChannel | None = None
        self.channels: dict[str, discord.TextChannel] = {}
        self.webhooks: dict[str, discord.Webhook] = {}
        self._renewing: dict[str, asyncio.Lock] = {}
        self._claw_of: dict[int, str] = {}
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._action: type[Any] | None = None

    def _mentions(self) -> discord.AllowedMentions:
        return discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=[discord.Object(self.operator_id)],
            replied_user=False,
        )

    # Lifecycle

    async def start(
        self, handler: transport.Handler, claws: Sequence[str]
    ) -> None:
        self._action = _action(handler)
        self.client.add_dynamic_items(self._action)
        self._register_commands(handler)

        @self.client.event
        async def on_ready() -> None:
            if not self._ready.is_set():
                await self._layout(claws)
                await self.tree.sync(guild=discord.Object(self.guild_id))
                self._ready.set()

        @self.client.event
        async def on_message(message: discord.Message) -> None:
            await self._on_message(handler, message)

        await self.client.login(self.token)
        self._task = asyncio.create_task(self.client.connect())
        ready = asyncio.create_task(self._ready.wait())
        await asyncio.wait(
            {self._task, ready}, return_when=asyncio.FIRST_COMPLETED
        )
        if not self._ready.is_set():
            ready.cancel()
            self._task.result()  # raises the connection error
            raise ConnectionError('disconnected before ready')

    async def close(self) -> None:
        await self.client.close()
        if self._task is not None:
            with contextlib.suppress(Exception):
                await self._task

    async def _layout(self, claws: Sequence[str]) -> None:
        guild = self.client.get_guild(self.guild_id)
        if guild is None:
            raise ConnectionError(f'bot is not in guild {self.guild_id}')
        category = discord.utils.get(guild.categories, name=CATEGORY)
        if category is None:
            category = await guild.create_category(CATEGORY)

        async def text_channel(name: str) -> discord.TextChannel:
            assert category is not None
            found = discord.utils.get(category.text_channels, name=name)
            return found or await category.create_text_channel(name)

        self.control = await text_channel(CONTROL_CHANNEL)
        for claw in claws:
            channel = await text_channel(channel_name(claw))
            self.channels[claw] = channel
            self._claw_of[channel.id] = claw
            self.webhooks[claw] = await self._webhook(claw)

    async def _webhook(self, claw: str) -> discord.Webhook:
        """The bot's webhook in the claw's channel, created if missing."""
        assert self.client.user is not None
        channel = self.channels[claw]
        hooks = await channel.webhooks()
        hook = next(
            (h for h in hooks if h.user and h.user.id == self.client.user.id),
            None,
        )
        if hook is None:
            avatar = self.avatars.get(claw)
            hook = await channel.create_webhook(
                name=claw, avatar=avatar.read_bytes() if avatar else None
            )
        return hook

    async def _renew_webhook(
        self, claw: str, stale: discord.Webhook
    ) -> discord.Webhook:
        """A webhook for the claw in place of `stale`, which was deleted."""
        lock = self._renewing.setdefault(claw, asyncio.Lock())
        async with lock:
            # Another report may have replaced it already.
            if self.webhooks[claw] is stale:
                log.warning('webhook for %s was deleted; renewing it', claw)
                self.webhooks[claw] = await self._webhook(claw)
            return self.webhooks[claw]

    # Inbound

    async def _on_message(
        self, handler: transport.Handler, message: discord.Message
    ) -> None:
        if message.author.bot or message.webhook_id is not None:
            return
        if message.guild is None or message.guild.id != self.guild_id:
            return
        if message.type not in (
            discord.MessageType.default,
            discord.MessageType.reply,
        ):
            return
        channel = message.channel
        thread_id = None
        if isinstance(channel, discord.Thread):
            claw = self._claw_of.get(channel.parent_id or 0)
            thread_id = channel.id
        else:
            claw = self._claw_of.get(channel.id)
        if claw is None:
            return
        try:
            await handler.on_message(
                message.author.id, claw, thread_id, message.id, message.content
            )
        except Exception:
            log.exception('handling message %s failed', message.id)

    def _register_commands(self, handler: transport.Handler) -> None:
        guild = discord.Object(self.guild_id)

        async def respond(
            interaction: discord.Interaction,
            name: str,
            options: dict[str, object],
        ) -> None:
            response = await handler.on_command(
                interaction.user.id, name, options
            )
            await interaction.response.send_message(
                response.text, ephemeral=response.private
            )

        @self.tree.command(name='status', guild=guild)
        async def status(interaction: discord.Interaction) -> None:
            """Show what every claw is doing."""
            await respond(interaction, 'status', {})

        @self.tree.command(name='budget', guild=guild)
        async def budget(interaction: discord.Interaction) -> None:
            """Show today's spend."""
            await respond(interaction, 'budget', {})

        @self.tree.command(name='trigger', guild=guild)
        async def trigger(
            interaction: discord.Interaction,
            claw: str,
            job: str | None = None,
            prompt: str | None = None,
        ) -> None:
            """Run a claw job now, or a one-off prompt."""
            await respond(
                interaction,
                'trigger',
                {'claw': claw, 'job': job, 'prompt': prompt},
            )

        @self.tree.command(name='pause', guild=guild)
        async def pause(
            interaction: discord.Interaction, target: str, abort: bool = False
        ) -> None:
            """Stop starting runs for a claw (or all); abort parks runs."""
            await respond(
                interaction, 'pause', {'target': target, 'abort': abort}
            )

        @self.tree.command(name='resume', guild=guild)
        async def resume(
            interaction: discord.Interaction, target: str
        ) -> None:
            """Unpause a claw (or all), or re-run a parked unit of work."""
            await respond(interaction, 'resume', {'target': target})

        @self.tree.command(name='reload', guild=guild)
        async def reload(interaction: discord.Interaction) -> None:
            """Reload claw configuration."""
            await respond(interaction, 'reload', {})

    # Outbound

    def _view(self, buttons: Sequence[transport.Button]) -> ui.View | None:
        if not buttons:
            return None
        assert self._action is not None
        view = ui.View(timeout=None)
        for component in buttons:
            button: ui.Button[Any] = ui.Button(
                label=component.label,
                style=discord.ButtonStyle.secondary,
                custom_id=component.custom_id,
            )
            view.add_item(self._action(button))
        return view

    async def create_thread(
        self, claw: str, name: str, from_message: int | None = None
    ) -> int:
        channel = self.channels[claw]
        if from_message is not None:
            message = channel.get_partial_message(from_message)
            thread = await message.create_thread(
                name=name[:100], auto_archive_duration=ARCHIVE_MINUTES
            )
        else:
            thread = await channel.create_thread(
                name=name[:100],
                type=discord.ChannelType.public_thread,
                auto_archive_duration=ARCHIVE_MINUTES,
            )
        return thread.id

    async def send(
        self,
        where: transport.Where,
        text: str,
        *,
        components: Sequence[transport.Button] = (),
        attachment: transport.Attachment | None = None,
    ) -> int:
        view = self._view(components)
        file = (
            discord.File(
                fp=io.BytesIO(attachment.data), filename=attachment.filename
            )
            if attachment
            else None
        )
        kwargs: dict[str, Any] = {'allowed_mentions': self._mentions()}
        if view is not None:
            kwargs['view'] = view
        if file is not None:
            kwargs['file'] = file
        if where.claw is None:
            assert self.control is not None
            message = await self.control.send(text, **kwargs)
            message_id: int = message.id
            return message_id
        assert where.thread_id is not None
        claw, thread = where.claw, discord.Object(where.thread_id)

        async def post(hook: discord.Webhook) -> int:
            with _gone():
                sent = await hook.send(
                    text, username=claw, thread=thread, wait=True, **kwargs
                )
            sent_id: int = sent.id
            return sent_id

        hook = self.webhooks[claw]
        try:
            return await post(hook)
        except discord.NotFound as e:
            if e.code != UNKNOWN_WEBHOOK:
                raise
        hook = await self._renew_webhook(claw, hook)
        if file is not None:
            file.reset()  # the first attempt read it
        return await post(hook)

    async def edit(
        self,
        where: transport.Where,
        message_id: int,
        text: str,
        *,
        components: Sequence[transport.Button] | None = None,
    ) -> None:
        kwargs: dict[str, Any] = {'allowed_mentions': self._mentions()}
        if components is not None:
            kwargs['view'] = self._view(components)
        if where.claw is None:
            assert self.control is not None
            partial = self.control.get_partial_message(message_id)
            await partial.edit(content=text, **kwargs)
            return
        assert where.thread_id is not None
        hook = self.webhooks[where.claw]
        try:
            with _gone():
                await hook.edit_message(
                    message_id,
                    content=text,
                    thread=discord.Object(where.thread_id),
                    **kwargs,
                )
        except discord.NotFound as e:
            if e.code != UNKNOWN_WEBHOOK:
                raise
            await self._renew_webhook(where.claw, hook)
            # Only the webhook that posted a message can edit it.
            raise transport.MessageGone(str(e)) from e

    async def react(
        self, where: transport.Where, message_id: int, emoji: str
    ) -> None:
        if where.claw is None or where.thread_id is None:
            return
        thread = self.client.get_channel(where.thread_id)
        if isinstance(thread, discord.Thread):
            await thread.get_partial_message(message_id).add_reaction(emoji)
