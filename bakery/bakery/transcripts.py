"""
Tool calls read back from pi session transcripts (`<claw>/sessions/*.jsonl`).

A transcript is one JSON entry per line, each with an ISO `timestamp`. Tool
calls are `toolCall` parts of assistant messages; their results are separate
`toolResult` messages, joined by `toolCallId`.
"""

import dataclasses
import datetime
import json
import pathlib
import re
from collections.abc import Iterator

# The sandbox's refusals as tools report them (as in the policy extension's
# SANDBOX_REFUSAL): seatbelt's EPERM, and the network proxy's 403.
SANDBOX_REFUSAL = re.compile(
    r'Operation not permitted|\bEPERM\b'
    r'|Connection blocked by network allowlist|blocked-by-allowlist'
    r'|Connection to \S+ blocked: resolved to'
    r'|CONNECT tunnel failed, response 403|Proxy response \(403\)'
    r'|[A-Z][a-z]+ "[^"]*": resolved to',
    re.IGNORECASE,
)
# The policy extension's note on results showing one.
SANDBOX_NOTE = re.compile(r"this claw's sandbox policy blocked it")


@dataclasses.dataclass(frozen=True)
class ToolCall:  # pylint: disable=too-many-instance-attributes
    claw: str
    session: pathlib.Path
    # The session's name: its unit of work, with `/` as `-`.
    name: str
    at: datetime.datetime
    tool: str
    args: dict[str, object]
    is_error: bool
    text: str

    def refusals(self) -> tuple[str, ...]:
        """
        The lines of its failed result showing the sandbox refusing.

        A call that succeeded refused nothing, whatever it printed: its text
        only quotes refusals (a file, a task, another claw's transcript).
        What a successful command hid (`cmd 2>&1 | tail`) is still in the
        sandbox's own violation log.
        """
        if not self.is_error:
            return ()
        return tuple(
            line.strip()
            for line in self.text.splitlines()
            if SANDBOX_REFUSAL.search(line) and not SANDBOX_NOTE.search(line)
        )


def _timestamp(raw: str) -> datetime.datetime:
    return datetime.datetime.fromisoformat(raw)


def _text(content: object) -> str:
    if not isinstance(content, list):
        return ''
    return '\n'.join(
        str(part.get('text', ''))
        for part in content
        if isinstance(part, dict) and part.get('type') == 'text'
    )


def tool_calls(
    claw: str, session: pathlib.Path, since: datetime.datetime
) -> Iterator[ToolCall]:
    """Calls in `session` whose results arrived at or after `since`."""
    name = session.stem.split('_', 1)[-1]
    calls: dict[str, tuple[str, dict[str, object]]] = {}
    with session.open(encoding='utf-8') as lines:
        for line in lines:
            entry = json.loads(line)
            if entry.get('type') == 'session_info' and entry.get('name'):
                name = str(entry['name'])
            if entry.get('type') != 'message':
                continue
            message = entry['message']
            if message.get('role') == 'assistant':
                for part in message.get('content') or []:
                    if part.get('type') == 'toolCall':
                        calls[part['id']] = (
                            part['name'],
                            part.get('arguments') or {},
                        )
                continue
            if message.get('role') != 'toolResult':
                continue
            at = _timestamp(entry['timestamp'])
            if at < since:
                continue
            tool, args = calls.get(
                message['toolCallId'], (message['toolName'], {})
            )
            yield ToolCall(
                claw=claw,
                session=session,
                name=name,
                at=at,
                tool=tool,
                args=args,
                is_error=bool(message.get('isError')),
                text=_text(message.get('content')),
            )


def claw_calls(
    claw_dir: pathlib.Path, since: datetime.datetime
) -> Iterator[ToolCall]:
    """Every call in a claw's transcripts since `since`."""
    sessions = claw_dir / 'sessions'
    if not sessions.is_dir():
        return
    for session in sorted(sessions.glob('*.jsonl')):
        modified = datetime.datetime.fromtimestamp(
            session.stat().st_mtime, datetime.UTC
        )
        if modified >= since:
            yield from tool_calls(claw_dir.name, session, since)
