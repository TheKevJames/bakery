"""
The Discord side of the gateway: turns runs into threads and the operator's
activity into gateway requests.

Each unit of work lives in one thread of its claw's channel; the mapping is
kept in the ledger so it survives restarts. Only the operator's messages,
clicks, and commands are acted on.
"""

import asyncio
import dataclasses
import logging
import time
from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import Sequence

from ..gateway import channel
from ..gateway import config
from ..gateway import core
from . import render
from . import transport

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 5.0
PREFIX = 'bakery'
THREAD_NAME_LIMIT = 90
NOT_OPERATOR = 'Only the operator can do that.'
EXPIRED = 'This request has expired.'


@dataclasses.dataclass
class _Status:
    where: transport.Where
    message_id: int
    edited_at: float


class Relay:
    def __init__(self, link: transport.Transport, operator_id: int) -> None:
        self.link = link
        self.operator_id = operator_id
        self._gateway: core.Gateway | None = None
        self._status: dict[int, _Status] = {}
        self._threads: dict[str, asyncio.Lock] = {}

    def bind(self, gateway: core.Gateway) -> None:
        self._gateway = gateway

    @property
    def gateway(self) -> core.Gateway:
        assert self._gateway is not None, 'relay is not bound to a gateway'
        return self._gateway

    @property
    def mention(self) -> str:
        return f'<@{self.operator_id}>'

    # Threads

    async def _thread(self, claw: str, work_key: str) -> transport.Where:
        """The unit's thread, created on first use."""
        lock = self._threads.setdefault(work_key, asyncio.Lock())
        async with lock:
            thread_id = self.gateway.runs.thread_of(work_key)
            if thread_id is None:
                thread_id = await self.link.create_thread(claw, work_key)
                self.gateway.runs.set_thread(work_key, thread_id)
        return transport.Where(claw, thread_id)

    async def _renew_thread(
        self, claw: str, work_key: str, gone: transport.Where
    ) -> transport.Where:
        """A new thread for the unit, in place of `gone`."""
        lock = self._threads.setdefault(work_key, asyncio.Lock())
        async with lock:
            # Another report may have replaced it already.
            if self.gateway.runs.thread_of(work_key) == gone.thread_id:
                thread_id = await self.link.create_thread(claw, work_key)
                self.gateway.runs.set_thread(work_key, thread_id)
                self.gateway.notify(
                    f'{work_key}: its thread was gone; started a new one'
                )
        return await self._thread(claw, work_key)

    async def _send(
        self,
        claw: str,
        work_key: str,
        text: str,
        *,
        components: Sequence[transport.Button] = (),
    ) -> tuple[transport.Where, int]:
        """Post to the unit's thread, replacing it if it is gone."""
        where = await self._thread(claw, work_key)
        try:
            return where, await self.link.send(
                where, text, components=components
            )
        except transport.ThreadGone:
            where = await self._renew_thread(claw, work_key, where)
            return where, await self.link.send(
                where, text, components=components
            )

    async def _edit_status(
        self, info: channel.RunInfo, status: _Status, text: str
    ) -> None:
        """Edit a run's status message, or post a new one if it is gone."""
        try:
            await self.link.edit(status.where, status.message_id, text)
        except (transport.ThreadGone, transport.MessageGone):
            status.where, status.message_id = await self._send(
                info.claw, info.work_key, text
            )

    # Channel: run reporting

    async def notice(self, text: str) -> None:
        await self.link.send(transport.Where(None), render.clip(text))

    async def run_started(self, info: channel.RunInfo) -> None:
        if info.silent_ok:
            return
        where, message_id = await self._send(
            info.claw, info.work_key, render.progress(info)
        )
        self._status[info.run_id] = _Status(
            where, message_id, time.monotonic()
        )

    async def run_progress(self, info: channel.RunInfo) -> None:
        status = self._status.get(info.run_id)
        now = time.monotonic()
        if status is None or now - status.edited_at < PROGRESS_INTERVAL:
            return
        status.edited_at = now
        await self._edit_status(info, status, render.progress(info))

    async def run_finished(self, info: channel.RunInfo) -> None:
        attention = info.status in render.NEEDS_ATTENTION
        status = self._status.pop(info.run_id, None)
        if (
            status is None
            and info.silent_ok
            and not (info.final_text or attention)
        ):
            return
        if status is not None:
            await self._edit_status(info, status, render.outcome(info))
            where = status.where
        else:
            where, _ = await self._send(
                info.claw, info.work_key, render.outcome(info)
            )
        if info.final_text and info.final_text.strip():
            await self._reply(where, info.final_text)
        if attention:
            resume = transport.Button(
                f'{PREFIX}:resume:{info.run_id}', 'Resume'
            )
            await self.link.send(
                where,
                render.clip(
                    f'{self.mention} {info.work_key} {info.status}:'
                    f' {info.reason}'
                ),
                components=(resume,),
            )

    async def _reply(self, where: transport.Where, text: str) -> None:
        parts = render.chunks(text)
        if parts is None:
            attachment = transport.Attachment('reply.md', text.encode())
            await self.link.send(
                where, 'The reply is attached.', attachment=attachment
            )
            return
        for part in parts:
            await self.link.send(where, part)

    # Handler: inbound from Discord

    async def on_message(
        self,
        author_id: int,
        claw: str,
        thread_id: int | None,
        message_id: int,
        text: str,
    ) -> None:
        if author_id != self.operator_id or not text.strip():
            return
        if thread_id is None:
            name = render.clip(text.strip().splitlines()[0], THREAD_NAME_LIMIT)
            thread_id = await self.link.create_thread(
                claw, name, from_message=message_id
            )
            work_key = f'{claw}/thread-{thread_id}'
            self.gateway.runs.set_thread(work_key, thread_id)
        else:
            known = self.gateway.runs.work_key_of_thread(thread_id)
            if known is None:
                return
            work_key = known
        where = transport.Where(claw, thread_id)
        try:
            self.gateway.message(claw, work_key, text)
        except (core.GatewayError, config.ConfigError) as e:
            await self.link.send(where, render.clip(f'⚠️ {e}'))
            return
        await self.link.react(where, message_id, '👀')

    async def on_component(self, author_id: int, custom_id: str) -> str:
        if author_id != self.operator_id:
            return NOT_OPERATOR
        parts = custom_id.split(':')
        if parts[:2] == [PREFIX, 'resume'] and len(parts) == 3:
            return self._click_resume(int(parts[2]))
        return EXPIRED

    def _click_resume(self, run_id: int) -> str:
        work_key = self.gateway.runs.work_key_of(run_id)
        if work_key is None:
            return EXPIRED
        try:
            self.gateway.resume(work_key)
        except core.GatewayError as e:
            return str(e)
        return f'Resuming {work_key}.'

    async def on_command(
        self, author_id: int, name: str, options: dict[str, object]
    ) -> transport.Response:
        if author_id != self.operator_id:
            return transport.Response(NOT_OPERATOR, private=True)

        command = self._commands().get(name)
        if command is None:
            return transport.Response(f'unknown command: {name}', private=True)
        try:
            return transport.Response(render.clip(command(options)))
        except (core.GatewayError, config.ConfigError) as e:
            return transport.Response(render.clip(f'⚠️ {e}'), private=True)

    def _commands(self) -> dict[str, Callable[[Mapping[str, object]], str]]:
        gateway = self.gateway
        return {
            'status': lambda _: render.status(gateway.status()),
            'budget': lambda _: render.budget(gateway.budget()),
            'trigger': self._trigger,
            'pause': self._pause,
            'resume': self._resume,
            'reload': self._reload,
        }

    def _trigger(self, options: Mapping[str, object]) -> str:
        work_key = self.gateway.trigger(
            _required(options, 'claw'),
            _optional(options, 'job'),
            _optional(options, 'prompt'),
        )
        return render.triggered(
            work_key, collecting=self.gateway.collecting(work_key)
        )

    def _pause(self, options: Mapping[str, object]) -> str:
        target = _required(options, 'target')
        self.gateway.pause(target, abort=bool(options.get('abort')))
        return f'paused {target}'

    def _resume(self, options: Mapping[str, object]) -> str:
        target = _required(options, 'target')
        self.gateway.resume(target)
        return f'resumed {target}'

    def _reload(self, options: Mapping[str, object]) -> str:
        del options
        self.gateway.reload()
        return 'reloaded'


def _optional(options: Mapping[str, object], key: str) -> str | None:
    value = options.get(key)
    return None if value is None else str(value)


def _required(options: Mapping[str, object], key: str) -> str:
    value = _optional(options, key)
    if value is None:
        raise core.GatewayError(f'missing {key}')
    return value
