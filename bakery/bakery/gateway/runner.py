"""
Execute one run: send a prompt to a pi child and follow it until it settles,
enforcing the run's budget.

On any breach the run is aborted and parked (resumable later); extension
errors and model errors fail it. Requests from elsewhere in the gateway (eg.
`pause --abort`) arrive as synthetic `bakery_abort` records on the child's
event queue, so they are handled in order with pi's own events.
"""

import asyncio
import dataclasses
import time

from . import channel as channel_
from . import ledger
from . import rpc

ABORT_EVENT = 'bakery_abort'
# How long an aborted run may take to settle before it is given up on.
ABORT_SETTLE_TIMEOUT = 30.0
SILENT_REPLY = 'NO_REPLY'

Stop = tuple[ledger.Status, str]


@dataclasses.dataclass(frozen=True)
class Budget:
    cost_usd: float
    turns: int
    seconds: float


def request_abort(
    proc: rpc.PiProcess, status: ledger.Status, reason: str
) -> None:
    proc.events.put_nowait(
        {'type': ABORT_EVENT, 'status': status, 'reason': reason}
    )


def _text(message: dict[str, object]) -> str:
    content = message.get('content')
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ''
    return ''.join(
        str(block.get('text', ''))
        for block in content
        if isinstance(block, dict) and block.get('type') == 'text'
    )


def _cost(message: dict[str, object]) -> float:
    usage = message.get('usage')
    cost = usage.get('cost') if isinstance(usage, dict) else None
    total = cost.get('total') if isinstance(cost, dict) else None
    return float(total) if isinstance(total, (int, float)) else 0.0


class _Run:
    """The event loop of one run; `stop` is set by the first breach."""

    # pylint: disable=too-many-instance-attributes

    def __init__(
        self,
        proc: rpc.PiProcess,
        info: channel_.RunInfo,
        budget: Budget,
        channel: channel_.Channel,
        runs: ledger.Ledger,
    ) -> None:
        self.proc = proc
        self.info = info
        self.budget = budget
        self.channel = channel
        self.runs = runs
        self.deadline = time.monotonic() + budget.seconds
        self.stop: Stop | None = None
        self.model_error: str | None = None

    async def _halt(self, status: ledger.Status, reason: str) -> None:
        if self.stop is not None:
            return
        self.stop = (status, reason)
        await self.proc.command({'type': 'abort'})

    async def _on_assistant(self, message: dict[str, object]) -> None:
        self.info.cost_usd += _cost(message)
        self.info.final_text = _text(message)
        if message.get('stopReason') == 'error':
            self.model_error = str(message.get('errorMessage', 'model error'))
        self.runs.record(
            self.info.run_id,
            cost_usd=self.info.cost_usd,
            turns=self.info.turns,
        )
        if self.info.cost_usd >= self.budget.cost_usd:
            await self._halt(
                ledger.Status.parked,
                f'cost limit reached (${self.budget.cost_usd:.2f})',
            )

    async def _on_turn_end(self, event: dict[str, object]) -> None:
        self.info.turns += 1
        self.info.current_tool = None
        self.runs.record(
            self.info.run_id,
            cost_usd=self.info.cost_usd,
            turns=self.info.turns,
        )
        await self.channel.run_progress(self.info)
        if self.info.turns >= self.budget.turns and event.get('toolResults'):
            await self._halt(
                ledger.Status.parked,
                f'turn limit reached ({self.budget.turns})',
            )

    async def _on_dialog(self, event: dict[str, object]) -> None:
        asked = time.monotonic()
        answer = await self.channel.dialog(self.info, event)
        # Waiting on a human does not count against the time limit.
        self.deadline += time.monotonic() - asked
        await self.proc.respond_ui(str(event['id']), answer)

    async def handle(self, event: dict[str, object]) -> bool:
        """Handle one event; True once the run has settled."""
        kind = event.get('type')
        message = event.get('message')
        if kind == 'agent_settled':
            return True
        if kind == 'message_end' and isinstance(message, dict):
            if message.get('role') == 'assistant':
                await self._on_assistant(message)
        elif kind == 'turn_end':
            await self._on_turn_end(event)
        elif kind == 'tool_execution_start':
            self.info.current_tool = str(event.get('toolName'))
            await self.channel.run_progress(self.info)
        elif kind == 'extension_error':
            await self._halt(
                ledger.Status.failed,
                f'extension error in {event.get("extensionPath")}:'
                f' {event.get("error")}',
            )
        elif kind == 'extension_ui_request':
            if event.get('method') in channel_.DIALOG_METHODS:
                await self._on_dialog(event)
        elif kind == ABORT_EVENT:
            await self._halt(
                ledger.Status(str(event['status'])), str(event['reason'])
            )
        return False

    async def follow(self) -> None:
        while True:
            if self.stop is None:
                timeout = self.deadline - time.monotonic()
            else:
                timeout = ABORT_SETTLE_TIMEOUT
            try:
                event = await asyncio.wait_for(
                    self.proc.next_event(), max(timeout, 0)
                )
            except TimeoutError as e:
                if self.stop is not None:
                    raise rpc.RpcError('pi did not settle after abort') from e
                minutes = self.budget.seconds / 60
                await self._halt(
                    ledger.Status.parked, f'time limit reached ({minutes:g}m)'
                )
                continue
            if await self.handle(event):
                return

    def outcome(self) -> Stop | tuple[ledger.Status, None]:
        if self.stop is not None:
            return self.stop
        if self.model_error is not None:
            return (ledger.Status.failed, self.model_error)
        return (ledger.Status.settled, None)


async def execute(
    proc: rpc.PiProcess,
    info: channel_.RunInfo,
    prompt: str,
    budget: Budget,
    channel: channel_.Channel,
    runs: ledger.Ledger,
) -> None:
    """
    Run `prompt` to completion, recording the outcome on `info`.

    The caller must drain events left over from earlier runs first.
    """
    run = _Run(proc, info, budget, channel, runs)
    data = await proc.command({'type': 'prompt', 'message': prompt})
    if data.get('disposition') != 'handled':
        await run.follow()
    info.status, info.reason = run.outcome()
    info.current_tool = None
    if info.silent_ok and (info.final_text or '').strip() == SILENT_REPLY:
        info.final_text = None
