"""Real pi, run against a fake OpenAI-compatible model."""

import dataclasses
import http.server
import json
import pathlib
import subprocess

REPO = pathlib.Path(__file__).resolve().parents[2]
FAKE_PROVIDER = pathlib.Path(__file__).parent / 'fake_provider.ts'
REPLY = 'OK'


@dataclasses.dataclass
class FakeLLM:
    """An OpenAI-compatible chat endpoint that records every request."""

    url: str
    requests: list[dict[str, object]]

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


def _chunk(delta: dict[str, object], finish: str | None) -> bytes:
    body = {
        'id': 'fake',
        'object': 'chat.completion.chunk',
        'model': 'echo',
        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}],
    }
    if finish:
        body['usage'] = {
            'prompt_tokens': 1,
            'completion_tokens': 1,
            'total_tokens': 2,
        }
    return f'data: {json.dumps(body)}\n\n'.encode()


def handler(requests: list[dict[str, object]]) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers['Content-Length'])
            requests.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(
                _chunk({'role': 'assistant', 'content': REPLY}, None)
            )
            self.wfile.write(_chunk({}, 'stop'))
            self.wfile.write(b'data: [DONE]\n\n')

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
