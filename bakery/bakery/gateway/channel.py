"""
The gateway's view of the human-facing channel (Discord).

The gateway reports run progress and forwards pi's extension dialogs through
this interface; it never talks to Discord directly.
"""

import dataclasses
import time
from typing import Protocol

from . import ledger

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


class Channel(Protocol):
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
