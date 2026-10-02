"""
The Discord Transport, via discord.py.

Kept thin: no decisions are made here, so the relay's behaviour can be tested
against a fake transport. Layout (category, channels, webhooks) is created on
first connect if missing.

Claws post through one bot-owned webhook per channel, so each has its own
name and avatar. Because the bot owns them, their messages can carry
interactive components, whose clicks are delivered to the bot.
"""

import asyncio
import contextlib
import io
import logging
import pathlib
import re
from collections.abc import Sequence
from typing import Any

import discord
from discord import app_commands
from discord import ui

from . import transport

log = logging.getLogger(__name__)

CATEGORY = 'bakery'
CONTROL_CHANNEL = 'bakery'
# 7 days: units of work (eg. a build ticket's PR review) can idle for days.
ARCHIVE_MINUTES = 10080
STYLES = {
    'primary': discord.ButtonStyle.primary,
    'secondary': discord.ButtonStyle.secondary,
    'success': discord.ButtonStyle.success,
    'danger': discord.ButtonStyle.danger,
}


def channel_name(claw: str) -> str:
    return f'{CONTROL_CHANNEL}-{claw}'


def _items(handler: transport.Handler) -> tuple[type[Any], type[Any]]:
    """Component classes that dispatch to `handler`, even across restarts."""

    async def dispatch(
        interaction: discord.Interaction, custom_id: str, value: str | None
    ) -> None:
        reply = await handler.on_component(
            interaction.user.id, custom_id, value
        )
        await interaction.response.send_message(reply, ephemeral=True)

    # Templates must not overlap: a match of the wrong item type fails.
    class Action(
        ui.DynamicItem[ui.Button[Any]],
        template=r'bakery:(?:dlg:[^:]+:(?:\d+|x)|resume:\d+)',
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
            await dispatch(interaction, str(self.item.custom_id), None)

    class Choice(
        ui.DynamicItem[ui.Select[Any]], template=r'bakery:dlg:[^:]+:sel'
    ):
        def __init__(self, select: ui.Select[Any]) -> None:
            super().__init__(select)

        @classmethod
        async def from_custom_id(
            cls,
            interaction: discord.Interaction,
            item: ui.Item[Any],
            match: re.Match[str],
            /,
        ) -> 'Choice':
            del interaction, match
            assert isinstance(item, ui.Select)
            return cls(item)

        async def callback(self, interaction: discord.Interaction) -> None:
            values = (interaction.data or {}).get('values') or [None]
            await dispatch(interaction, str(self.item.custom_id), values[0])

    return Action, Choice


class DiscordTransport:
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self,
        token: str,
        guild_id: int,
        owner_id: int,
        avatars: dict[str, pathlib.Path],
    ) -> None:
        self.token = token
        self.guild_id = guild_id
        self.owner_id = owner_id
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
        self._claw_of: dict[int, str] = {}
        self._ready = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._action: type[Any] | None = None
        self._choice: type[Any] | None = None

    def _mentions(self) -> discord.AllowedMentions:
        return discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=[discord.Object(self.owner_id)],
            replied_user=False,
        )

    # Lifecycle

    async def start(
        self, handler: transport.Handler, claws: Sequence[str]
    ) -> None:
        self._action, self._choice = _items(handler)
        self.client.add_dynamic_items(self._action, self._choice)
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
        assert self.client.user is not None
        for claw in claws:
            channel = await text_channel(channel_name(claw))
            self.channels[claw] = channel
            self._claw_of[channel.id] = claw
            hooks = await channel.webhooks()
            hook = next(
                (
                    h
                    for h in hooks
                    if h.user and h.user.id == self.client.user.id
                ),
                None,
            )
            if hook is None:
                avatar = self.avatars.get(claw)
                hook = await channel.create_webhook(
                    name=claw, avatar=avatar.read_bytes() if avatar else None
                )
            self.webhooks[claw] = hook

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

    def _view(
        self, components: Sequence[transport.Component]
    ) -> ui.View | None:
        if not components:
            return None
        assert self._action is not None and self._choice is not None
        view = ui.View(timeout=None)
        for component in components:
            if isinstance(component, transport.Button):
                button: ui.Button[Any] = ui.Button(
                    label=component.label,
                    style=STYLES[component.style],
                    custom_id=component.custom_id,
                )
                view.add_item(self._action(button))
            else:
                select: ui.Select[Any] = ui.Select(
                    custom_id=component.custom_id,
                    placeholder=component.placeholder or None,
                    options=[
                        discord.SelectOption(
                            label=option[:100], value=str(index)
                        )
                        for index, option in enumerate(component.options)
                    ],
                )
                view.add_item(self._choice(select))
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
        components: Sequence[transport.Component] = (),
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
        sent = await self.webhooks[where.claw].send(
            text,
            username=where.claw,
            thread=discord.Object(where.thread_id),
            wait=True,
            **kwargs,
        )
        sent_id: int = sent.id
        return sent_id

    async def edit(
        self,
        where: transport.Where,
        message_id: int,
        text: str,
        *,
        components: Sequence[transport.Component] | None = None,
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
        await self.webhooks[where.claw].edit_message(
            message_id,
            content=text,
            thread=discord.Object(where.thread_id),
            **kwargs,
        )

    async def react(
        self, where: transport.Where, message_id: int, emoji: str
    ) -> None:
        if where.claw is None or where.thread_id is None:
            return
        thread = self.client.get_channel(where.thread_id)
        if isinstance(thread, discord.Thread):
            await thread.get_partial_message(message_id).add_reaction(emoji)
