"""
The gateway's view of the human-facing channel (Discord).

The gateway reports run progress and forwards pi's extension dialogs through
this interface; it never talks to Discord directly.
"""

import dataclasses
import logging
import time
from collections.abc import Awaitable
from collections.abc import Callable
from typing import Protocol

from . import ledger

log = logging.getLogger(__name__)

# Extension UI methods that block pi until answered; the rest
# (notify, setStatus, ...) are fire-and-forget.
DIALOG_METHODS = frozenset({'select', 'confirm', 'input', 'editor'})


@dataclasses.dataclass
class RunInfo:
    """Live state of one run, updated in place as it progresses."""

    # pylint: disable=too-many-instance-attributes

    run_id: int
    claw: str
    work_key: str
    session_id: str
    trigger: str
    # A heartbeat-style run: a NO_REPLY result should leave no trace.
    silent_ok: bool
    started_at: float = dataclasses.field(default_factory=time.time)
    status: ledger.Status = ledger.Status.running
    reason: str | None = None
    cost_usd: float = 0.0
    turns: int = 0
    current_tool: str | None = None
    # The last assistant message; None when the run was silent.
    final_text: str | None = None
    # The state-repo commit of the claw's changes, once the run has finished;
    # None if it changed nothing.
    state_commit: str | None = None


class Channel(Protocol):
    async def notice(self, text: str) -> None:
        """A gateway-level announcement (startup, budget, config errors)."""

    async def run_started(self, info: RunInfo) -> None: ...

    async def run_progress(self, info: RunInfo) -> None:
        """Called after each tool start and each turn; may be throttled."""

    async def run_finished(self, info: RunInfo) -> None: ...

    async def dialog(
        self, info: RunInfo, request: dict[str, object]
    ) -> dict[str, object]:
        """
        Answer one pi `extension_ui_request` dialog.

        Returns the response fields, eg. `{"confirmed": True}`,
        `{"value": "..."}`, or `{"cancelled": True}`. Time spent here does
        not count against the run's time limit.
        """
        ...


class Reporting:
    """
    A channel whose reports never fail a run: a broken thread or webhook
    costs the operator the run's trail, not the run. Failures are logged, and
    the first of each run is announced in a notice. Dialogs still raise, as a
    run cannot carry on without its answer.
    """

    def __init__(self, channel: Channel) -> None:
        self.channel = channel
        # Runs whose report failures have been announced.
        self._announced: set[int] = set()

    async def notice(self, text: str) -> None:
        try:
            await self.channel.notice(text)
        except Exception:
            log.exception('channel failed posting notice: %s', text)

    async def run_started(self, info: RunInfo) -> None:
        await self._report(self.channel.run_started, info)

    async def run_progress(self, info: RunInfo) -> None:
        await self._report(self.channel.run_progress, info)

    async def run_finished(self, info: RunInfo) -> None:
        try:
            await self._report(self.channel.run_finished, info)
        finally:
            self._announced.discard(info.run_id)

    async def dialog(
        self, info: RunInfo, request: dict[str, object]
    ) -> dict[str, object]:
        return await self.channel.dialog(info, request)

    async def _report(
        self, report: Callable[[RunInfo], Awaitable[None]], info: RunInfo
    ) -> None:
        try:
            await report(info)
        except Exception as e:
            log.exception('channel failed reporting %s', info.work_key)
            if info.run_id not in self._announced:
                self._announced.add(info.run_id)
                await self.notice(
                    f'{info.work_key}: could not report run {info.run_id}'
                    f' to its thread ({e}); see gateway.log'
                )
