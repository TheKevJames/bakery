"""
The Discord side of the gateway: turns runs into threads and my Discord
activity into gateway requests.

Each unit of work lives in one thread of its claw's channel; the mapping is
kept in the ledger so it survives restarts. Only the owner's messages,
clicks, and commands are acted on.
"""

import asyncio
import contextlib
import dataclasses
import logging
import time
from collections.abc import Callable
from collections.abc import Mapping

from ..gateway import channel
from ..gateway import config
from ..gateway import core
from . import render
from . import transport

log = logging.getLogger(__name__)

PROGRESS_INTERVAL = 5.0
PREFIX = 'bakery'
SELECT_BUTTON_LIMIT = 5
SELECT_OPTION_LIMIT = 25
THREAD_NAME_LIMIT = 90
NOT_OWNER = 'Only the owner can do that.'
TIMEOUT_GRACE = 2.0
EXPIRED = 'This request has expired.'


@dataclasses.dataclass
class _Dialog:
    work_key: str
    method: str
    options: tuple[str, ...]
    answer: asyncio.Future[dict[str, object]]


@dataclasses.dataclass
class _Status:
    where: transport.Where
    message_id: int
    edited_at: float


def _request_text(request: dict[str, object], key: str) -> str:
    value = request.get(key)
    return value if isinstance(value, str) else ''


class Relay:
    # pylint: disable=too-many-instance-attributes
    def __init__(self, link: transport.Transport, owner_id: int) -> None:
        self.link = link
        self.owner_id = owner_id
        self._gateway: core.Gateway | None = None
        self._status: dict[int, _Status] = {}
        self._dialogs: dict[str, _Dialog] = {}
        self._threads: dict[str, asyncio.Lock] = {}

    def bind(self, gateway: core.Gateway) -> None:
        self._gateway = gateway

    @property
    def gateway(self) -> core.Gateway:
        assert self._gateway is not None, 'relay is not bound to a gateway'
        return self._gateway

    @property
    def mention(self) -> str:
        return f'<@{self.owner_id}>'

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

    # Channel: run reporting

    async def notice(self, text: str) -> None:
        await self.link.send(transport.Where(None), render.clip(text))

    async def run_started(self, info: channel.RunInfo) -> None:
        if info.silent_ok:
            return
        where = await self._thread(info.claw, info.work_key)
        message_id = await self.link.send(where, render.progress(info))
        self._status[info.run_id] = _Status(
            where, message_id, time.monotonic()
        )

    async def run_progress(self, info: channel.RunInfo) -> None:
        status = self._status.get(info.run_id)
        now = time.monotonic()
        if status is None or now - status.edited_at < PROGRESS_INTERVAL:
            return
        status.edited_at = now
        await self.link.edit(
            status.where, status.message_id, render.progress(info)
        )

    async def run_finished(self, info: channel.RunInfo) -> None:
        attention = info.status in render.NEEDS_ATTENTION
        status = self._status.pop(info.run_id, None)
        if (
            status is None
            and info.silent_ok
            and not (info.final_text or attention)
        ):
            return
        where = (
            status.where
            if status
            else (await self._thread(info.claw, info.work_key))
        )
        if status is not None:
            await self.link.edit(
                where, status.message_id, render.outcome(info)
            )
        else:
            await self.link.send(where, render.outcome(info))
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

    # Channel: dialogs

    def _render_dialog(
        self, request_id: str, request: dict[str, object]
    ) -> tuple[str, tuple[str, ...], tuple[transport.Component, ...]]:
        method = request.get('method')
        title = _request_text(request, 'title')
        base = f'{PREFIX}:dlg:{request_id}'
        cancel = transport.Button(f'{base}:x', 'Cancel')
        if method == 'confirm':
            body = _request_text(request, 'message')
            return (
                f'{self.mention} **{title}**\n{body}',
                (),
                (
                    transport.Button(f'{base}:1', 'Approve', 'success'),
                    transport.Button(f'{base}:0', 'Deny', 'danger'),
                ),
            )
        if method == 'select':
            raw = request.get('options')
            options = (
                tuple(str(x) for x in raw) if isinstance(raw, list) else ()
            )
            options = options[:SELECT_OPTION_LIMIT]
            components: tuple[transport.Component, ...]
            if len(options) <= SELECT_BUTTON_LIMIT:
                components = tuple(
                    transport.Button(f'{base}:{i}', render.clip(option, 80))
                    for i, option in enumerate(options)
                )
            else:
                components = (
                    transport.Select(f'{base}:sel', options, 'Choose…'),
                )
            return (
                f'{self.mention} **{title}**',
                options,
                (*components, cancel),
            )
        hint = _request_text(request, 'placeholder') or _request_text(
            request, 'prefill'
        )
        return (
            f'{self.mention} **{title}**\n{hint}\n'
            '_Reply in this thread to answer._',
            (),
            (cancel,),
        )

    async def _post_dialog(
        self, info: channel.RunInfo, request: dict[str, object]
    ) -> tuple[_Dialog, transport.Where, int, str]:
        """Post a dialog's question; returns it with where it was posted."""
        request_id = str(request['id'])
        where = await self._thread(info.claw, info.work_key)
        text, options, components = self._render_dialog(request_id, request)
        text = render.clip(text)
        pending = _Dialog(
            info.work_key,
            str(request.get('method')),
            options,
            asyncio.get_running_loop().create_future(),
        )
        # Before sending: the message is clickable before send() returns.
        self._dialogs[request_id] = pending
        try:
            message_id = await self.link.send(
                where, text, components=components
            )
        except BaseException:
            self._dialogs.pop(request_id, None)
            raise
        return pending, where, message_id, text

    async def dialog(
        self, info: channel.RunInfo, request: dict[str, object]
    ) -> dict[str, object]:
        pending, where, message_id, text = await self._post_dialog(
            info, request
        )
        timeout = request.get('timeout')
        seconds = timeout / 1000 if isinstance(timeout, (int, float)) else None
        loop = asyncio.get_running_loop()
        deadline = None if seconds is None else loop.time() + seconds
        outcome = 'cancelled'
        answer: dict[str, object] | None = None
        try:
            answer = await asyncio.wait_for(pending.answer, seconds)
            outcome = self._describe(answer)
        except TimeoutError:
            outcome = 'timed out'
        finally:
            # pi times dialogs out too, from when it sent the request, so it
            # usually expires first; the run then stops and cancels this
            # wait just before this deadline.
            if (
                answer is None
                and deadline is not None
                and loop.time() >= deadline - TIMEOUT_GRACE
            ):
                outcome = 'timed out'
            self._dialogs.pop(str(request['id']), None)
            with contextlib.suppress(Exception):
                await self.link.edit(
                    where,
                    message_id,
                    render.clip(f'{text}\n→ {outcome}'),
                    components=(),
                )
        return answer or {'cancelled': True}

    @staticmethod
    def _describe(answer: dict[str, object]) -> str:
        if answer.get('cancelled'):
            return 'cancelled'
        if 'confirmed' in answer:
            return 'approved' if answer['confirmed'] else 'denied'
        return f'answered: {render.clip(str(answer.get("value")), 200)}'

    def _resolve(self, request_id: str, answer: dict[str, object]) -> bool:
        pending = self._dialogs.get(request_id)
        if pending is None or pending.answer.done():
            return False
        pending.answer.set_result(answer)
        return True

    def _dialog_answer(
        self, pending: _Dialog, choice: str
    ) -> dict[str, object] | None:
        if choice == 'x':
            return {'cancelled': True}
        if pending.method == 'confirm':
            return {'confirmed': choice == '1'}
        if pending.method == 'select' and choice.isdigit():
            index = int(choice)
            if index < len(pending.options):
                return {'value': pending.options[index]}
        return None

    # Handler: inbound from Discord

    async def on_message(
        self,
        author_id: int,
        claw: str,
        thread_id: int | None,
        message_id: int,
        text: str,
    ) -> None:
        if author_id != self.owner_id or not text.strip():
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
        for request_id, pending in list(self._dialogs.items()):
            if pending.work_key == work_key and pending.method in (
                'input',
                'editor',
            ):
                self._resolve(request_id, {'value': text})
                await self.link.react(where, message_id, '✅')
                return
        try:
            self.gateway.message(claw, work_key, text)
        except (core.GatewayError, config.ConfigError) as e:
            await self.link.send(where, render.clip(f'⚠️ {e}'))
            return
        await self.link.react(where, message_id, '👀')

    async def on_component(
        self, author_id: int, custom_id: str, value: str | None
    ) -> str:
        if author_id != self.owner_id:
            return NOT_OWNER
        parts = custom_id.split(':')
        if parts[:2] == [PREFIX, 'dlg'] and len(parts) == 4:
            return self._click_dialog(parts[2], parts[3], value)
        if parts[:2] == [PREFIX, 'resume'] and len(parts) == 3:
            return self._click_resume(int(parts[2]))
        return EXPIRED

    def _click_dialog(
        self, request_id: str, choice: str, value: str | None
    ) -> str:
        pending = self._dialogs.get(request_id)
        if pending is None:
            return EXPIRED
        if choice == 'sel' and value is not None:
            choice = value
        answer = self._dialog_answer(pending, choice)
        if answer is None or not self._resolve(request_id, answer):
            return EXPIRED
        return 'Done.'

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
        if author_id != self.owner_id:
            return transport.Response(NOT_OWNER, private=True)

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
