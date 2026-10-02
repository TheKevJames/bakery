"""Real pi, run against a fake OpenAI-compatible model."""

import collections
import dataclasses
import http.server
import json
import pathlib
import subprocess
import threading
import time
from collections.abc import Iterator

REPO = pathlib.Path(__file__).resolve().parents[2]
FAKE_PROVIDER = pathlib.Path(__file__).parent / 'fake_provider.ts'
# fake_provider.ts prices input at $1 per token, so a reply's `prompt_tokens`
# is exactly what it costs.
DOLLARS_PER_PROMPT_TOKEN = 1.0


@dataclasses.dataclass(frozen=True)
class Reply:
    """One scripted model response: text, or a single tool call."""

    text: str = 'OK'
    tool: str | None = None
    args: dict[str, object] = dataclasses.field(default_factory=dict)
    prompt_tokens: int = 0
    delay: float = 0.0


@dataclasses.dataclass
class FakeLLM:
    """
    An OpenAI-compatible chat endpoint that records every request.

    Replies are taken from `script` in order; once it is empty every request
    gets the default `Reply()`.
    """

    url: str
    requests: list[dict[str, object]]
    script: collections.deque[Reply] = dataclasses.field(
        default_factory=collections.deque
    )

    def queue(self, *replies: Reply) -> None:
        self.script.extend(replies)

    def system_prompt(self, index: int = -1) -> str:
        messages = self.requests[index]['messages']
        assert isinstance(messages, list)
        return '\n'.join(
            str(m['content']) for m in messages if m['role'] == 'system'
        )

    def transcript(self, index: int = -1) -> str:
        messages = self.requests[index]['messages']
        assert isinstance(messages, list)
        return '\n'.join(json.dumps(m) for m in messages)


def _chunk(
    delta: dict[str, object], finish: str | None, prompt_tokens: int = 0
) -> bytes:
    body: dict[str, object] = {
        'id': 'fake',
        'object': 'chat.completion.chunk',
        'model': 'echo',
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
    }
    if finish:
        body['usage'] = {
            'prompt_tokens': prompt_tokens,
            'completion_tokens': 1,
            'total_tokens': prompt_tokens + 1,
        }
    return f'data: {json.dumps(body)}\n\n'.encode()


def _render(reply: Reply) -> Iterator[bytes]:
    if reply.tool is None:
        yield _chunk({'role': 'assistant', 'content': reply.text}, None)
        yield _chunk({}, 'stop', reply.prompt_tokens)
    else:
        call = {
            'index': 0,
            'id': f'call_{time.monotonic_ns()}',
            'type': 'function',
            'function': {
                'name': reply.tool,
                'arguments': json.dumps(reply.args),
            },
        }
        yield _chunk({'role': 'assistant', 'tool_calls': [call]}, None)
        yield _chunk({}, 'tool_calls', reply.prompt_tokens)
    yield b'data: [DONE]\n\n'


def handler(llm: FakeLLM) -> type:
    lock = threading.Lock()

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers['Content-Length'])
            with lock:
                llm.requests.append(json.loads(self.rfile.read(length)))
                reply = llm.script.popleft() if llm.script else Reply()
            time.sleep(reply.delay)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            for chunk in _render(reply):
                self.wfile.write(chunk)

        def log_request(
            self, code: int | str = '-', size: int | str = '-'
        ) -> None:
            pass

    return Handler


@dataclasses.dataclass
class Pi:
    """Runs real pi in print mode against the fake model."""

    profile: pathlib.Path
    env: dict[str, str]
    cwd: pathlib.Path

    def run(
        self, text: str, *, resume: bool = False
    ) -> subprocess.CompletedProcess[str]:
        args = ['pi', '-p', text, '--model', 'fake/echo']
        args += ['-e', str(FAKE_PROVIDER)]
        if resume:
            args.append('--continue')
        return subprocess.run(
            args,
            cwd=self.cwd,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def prompt(self, text: str, *, resume: bool = False) -> str:
        """Run one prompt, requiring a clean run (no extension errors)."""
        result = self.run(text, resume=resume)
        assert result.returncode == 0, result.stderr
        assert not result.stderr, result.stderr
        return result.stdout
