"""
Execute one run: send a prompt to a pi child and follow it until it settles,
enforcing the run's budget.

On any breach the run is aborted and parked (resumable later); extension
errors and model errors fail it. Requests from elsewhere in the gateway (an
abort from `pause --abort`, a steering message from Discord) arrive as
synthetic records on the child's event queue, so they are handled in order
with pi's own events.
"""

import asyncio
import dataclasses
import time
from collections.abc import Awaitable
from collections.abc import Callable

from . import channel as channel_
from . import ledger
from . import rpc

ABORT_EVENT = 'bakery_abort'
STEER_EVENT = 'bakery_steer'
# A claw-only tool (pi/claw-extensions/ask-user.ts) that parks the run when
# its question cannot be answered now.
ASK_TOOL = 'ask_user'
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


def request_steer(proc: rpc.PiProcess, text: str) -> None:
    proc.events.put_nowait({'type': STEER_EVENT, 'text': text})


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


async def session_cost(proc: rpc.PiProcess) -> float:
    stats = await proc.command({'type': 'get_session_stats'})
    cost = stats.get('cost')
    return float(cost) if isinstance(cost, (int, float)) else 0.0


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
        baseline: float,
    ) -> None:
        self.proc = proc
        # The session's cost before this run; runs of a unit share a session.
        self.baseline = baseline
        self.info = info
        self.budget = budget
        self.channel = channel
        self.runs = runs
        self.deadline = time.monotonic() + budget.seconds
        self.stop: Stop | None = None
        self.model_error: str | None = None
        # A steer that lands just after pi settled starts a new pi run, which
        # settles separately; wait for every one of them.
        self.unsettled = 1
        # Dialogs are answered off the event loop so aborts and progress
        # still flow while a question waits, possibly for hours.
        self.dialogs: set[asyncio.Task[None]] = set()
        self.waiting_since: float | None = None

    async def _halt(self, status: ledger.Status, reason: str) -> None:
        if self.stop is not None:
            return
        self.stop = (status, reason)
        self.cancel_dialogs()
        await self.proc.command({'type': 'abort'})

    def cancel_dialogs(self) -> None:
        for task in self.dialogs:
            task.cancel()

    async def _on_assistant(self, message: dict[str, object]) -> None:
        # A NO_REPLY message (eg. after a memory flush, or a quiet heartbeat)
        # never replaces the run's reply.
        text = _text(message).strip()
        if text and text != SILENT_REPLY:
            self.info.final_text = text
        if message.get('stopReason') == 'error':
            self.model_error = str(message.get('errorMessage', 'model error'))
        # Provisional, so a costly message stops the run before its tool
        # calls execute; reconciled with pi's own accounting at turn ends.
        await self._charge(self.info.cost_usd + _cost(message))

    async def reconcile_cost(self, *, enforce: bool = True) -> None:
        """
        Take the run's cost from pi's session stats, which also count
        model calls outside assistant messages (eg. compaction summaries).
        """
        if self.proc.alive:
            cost = await session_cost(self.proc) - self.baseline
            await self._charge(cost, enforce=enforce)

    async def _charge(self, cost: float, *, enforce: bool = True) -> None:
        self.info.cost_usd = cost
        self.runs.record(
            self.info.run_id, cost_usd=cost, turns=self.info.turns
        )
        if enforce and cost >= self.budget.cost_usd:
            await self._halt(
                ledger.Status.parked,
                f'cost limit reached (${self.budget.cost_usd:.2f})',
            )

    async def _on_turn_end(self, event: dict[str, object]) -> None:
        self.info.turns += 1
        self.info.current_tool = None
        await self.reconcile_cost()
        await self.channel.run_progress(self.info)
        if self.info.turns >= self.budget.turns and event.get('toolResults'):
            await self._halt(
                ledger.Status.parked,
                f'turn limit reached ({self.budget.turns})',
            )

    def _on_dialog(self, event: dict[str, object]) -> None:
        if not self.dialogs:
            self.waiting_since = time.monotonic()
        task = asyncio.create_task(self._answer(event))
        self.dialogs.add(task)
        task.add_done_callback(self._answered)

    async def _answer(self, event: dict[str, object]) -> None:
        answer = await self.channel.dialog(self.info, event)
        if self.proc.alive:
            await self.proc.respond_ui(str(event['id']), answer)

    def _answered(self, task: asyncio.Task[None]) -> None:
        self.dialogs.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self.proc.events.put_nowait(
                {
                    'type': ABORT_EVENT,
                    'status': ledger.Status.failed,
                    'reason': f'answering a dialog failed: {task.exception()}',
                }
            )
        if not self.dialogs and self.waiting_since is not None:
            # Waiting on a human does not count against the time limit.
            self.deadline += time.monotonic() - self.waiting_since
            self.waiting_since = None

    async def handle(self, event: dict[str, object]) -> bool:
        """Handle one event; True once the run has settled."""
        kind = event.get('type')
        if kind == 'agent_settled':
            self.unsettled -= 1
            return self.unsettled == 0
        handler = self._handlers.get(str(kind))
        if handler is not None:
            await handler(event)
        return False

    @property
    def _handlers(
        self,
    ) -> dict[str, Callable[[dict[str, object]], Awaitable[None]]]:
        return {
            'message_end': self._on_message_end,
            'turn_end': self._on_turn_end,
            'compaction_end': self._on_compaction_end,
            'tool_execution_start': self._on_tool_start,
            'tool_execution_end': self._on_tool_end,
            'extension_error': self._on_extension_error,
            'extension_ui_request': self._on_ui_request,
            ABORT_EVENT: self._on_abort,
            STEER_EVENT: self._on_steer,
        }

    async def _on_message_end(self, event: dict[str, object]) -> None:
        message = event.get('message')
        if isinstance(message, dict) and message.get('role') == 'assistant':
            await self._on_assistant(message)

    async def _on_compaction_end(self, event: dict[str, object]) -> None:
        del event
        await self.reconcile_cost()

    async def _on_tool_start(self, event: dict[str, object]) -> None:
        self.info.current_tool = str(event.get('toolName'))
        await self.channel.run_progress(self.info)

    async def _on_extension_error(self, event: dict[str, object]) -> None:
        await self._halt(
            ledger.Status.failed,
            f'extension error in {event.get("extensionPath")}:'
            f' {event.get("error")}',
        )

    async def _on_ui_request(self, event: dict[str, object]) -> None:
        if event.get('method') in channel_.DIALOG_METHODS:
            self._on_dialog(event)

    async def _on_abort(self, event: dict[str, object]) -> None:
        await self._halt(
            ledger.Status(str(event['status'])), str(event['reason'])
        )

    async def _on_steer(self, event: dict[str, object]) -> None:
        await self._steer(str(event['text']))

    async def _on_tool_end(self, event: dict[str, object]) -> None:
        result = event.get('result')
        details = result.get('details') if isinstance(result, dict) else None
        if event.get('toolName') == ASK_TOOL and isinstance(details, dict):
            if details.get('park'):
                await self._halt(
                    ledger.Status.parked,
                    f'waiting for an answer: {details.get("question")}',
                )

    async def _steer(self, text: str) -> None:
        if self.stop is not None:
            return
        data = await self.proc.command(
            {'type': 'prompt', 'message': text, 'streamingBehavior': 'steer'}
        )
        if data.get('disposition') == 'started':
            self.unsettled += 1

    async def follow(self) -> None:
        while True:
            timeout: float | None
            if self.stop is not None:
                timeout = ABORT_SETTLE_TIMEOUT
            elif self.dialogs:
                timeout = None
            else:
                timeout = max(self.deadline - time.monotonic(), 0)
            try:
                event = await asyncio.wait_for(self.proc.next_event(), timeout)
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
    run = _Run(proc, info, budget, channel, runs, await session_cost(proc))
    try:
        data = await proc.command({'type': 'prompt', 'message': prompt})
        if data.get('disposition') != 'handled':
            await run.follow()
        # Settled runs are not stopped retroactively; their full cost still
        # counts towards the daily budgets.
        await run.reconcile_cost(enforce=False)
    finally:
        run.cancel_dialogs()
    info.status, info.reason = run.outcome()
    info.current_tool = None
