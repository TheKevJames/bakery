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

# The reasons pi/claw-extensions/policy blocks a call with.
POLICY_BLOCKS = re.compile(
    r"is not allowed by this claw's policy$"
    r"|is not writable under this claw's policy$"
    r'|is protected and cannot be read$'
    r'|contains protected paths; search a narrower directory, or use bash$'
    r'|denied by the user$'
)
# Transcripts from before the policy extension reported sandbox violations
# only show the OS's refusal.
SANDBOX_DENIAL = re.compile(r'operation not permitted', re.IGNORECASE)


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
    # Lines the sandbox reported for a bash call.
    violations: tuple[str, ...]

    def restriction(self) -> tuple[str, tuple[str, ...]] | None:
        """('policy' | 'sandbox', lines) if the claw's policy refused it."""
        if self.violations:
            return 'sandbox', self.violations
        if self.is_error and POLICY_BLOCKS.search(self.text.strip()):
            return 'policy', (self.text.strip(),)
        if self.tool == 'bash':
            denied = tuple(
                line.strip()
                for line in self.text.splitlines()
                if SANDBOX_DENIAL.search(line)
            )
            if denied:
                return 'sandbox', denied
        return None


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
            details = message.get('details')
            violations = (
                details.get('sandboxViolations') or ()
                if isinstance(details, dict)
                else ()
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
                violations=tuple(violations),
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
