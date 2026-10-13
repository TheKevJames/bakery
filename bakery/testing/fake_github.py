"""A fake GitHub REST API serving canned responses, recording requests."""

import contextlib
import dataclasses
import http.server
import io
import json
import threading
import time
import zipfile
from collections.abc import Generator


@dataclasses.dataclass
class FakeGitHub:
    url: str = ''
    # path (with query) -> JSON-able body, or bytes
    routes: dict[str, object] = dataclasses.field(default_factory=dict)
    # path -> (next page path)
    next_pages: dict[str, str] = dataclasses.field(default_factory=dict)
    # path -> redirect target path
    redirects: dict[str, str] = dataclasses.field(default_factory=dict)
    requests: list[tuple[str, str | None]] = dataclasses.field(
        default_factory=list
    )
    # Seconds each response takes, to keep a collection in progress.
    delay: float = 0.0


def logs_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


def _handler(fake: FakeGitHub) -> type:
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            time.sleep(fake.delay)
            auth = self.headers.get('Authorization')
            fake.requests.append((self.path, auth))
            if self.path in fake.redirects:
                self.send_response(302)
                self.send_header(
                    'Location', fake.url + fake.redirects[self.path]
                )
                self.end_headers()
                return
            if self.path not in fake.routes:
                self.send_error(404)
                return
            body = fake.routes[self.path]
            data = (
                body if isinstance(body, bytes) else json.dumps(body).encode()
            )
            self.send_response(200)
            if self.path in fake.next_pages:
                link = fake.url + fake.next_pages[self.path]
                self.send_header('Link', f'<{link}>; rel="next"')
            self.end_headers()
            self.wfile.write(data)

        def log_request(
            self, code: int | str = '-', size: int | str = '-'
        ) -> None:
            pass

    return Handler


@contextlib.contextmanager
def serve() -> Generator[FakeGitHub]:
    fake = FakeGitHub()
    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), _handler(fake))
    fake.url = f'http://127.0.0.1:{server.server_port}'
    thread = threading.Thread(
        target=server.serve_forever, args=(0.01,), daemon=True
    )
    thread.start()
    try:
        yield fake
    finally:
        server.shutdown()
