import asyncio
import http.server
import json
import threading
from collections.abc import Iterator

import aiohttp
import discord
import pytest

from bakery.chat import discord_transport
from bakery.chat import transport
from testing import servers


class FakeDiscord(servers.LocalServer):
    """Discord's API, answering every request with one JSON error."""

    answer: tuple[int, int, str] = (500, 0, 'unset')


class Handler(http.server.BaseHTTPRequestHandler):
    def _answer(self) -> None:
        assert isinstance(self.server, FakeDiscord)
        self.rfile.read(int(self.headers.get('Content-Length', 0)))
        status, code, message = self.server.answer
        body = json.dumps({'code': code, 'message': message}).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PATCH = _answer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


@pytest.fixture(name='discord_api', scope='function')
def fixture_discord_api(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FakeDiscord]:
    server = FakeDiscord(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(
        'discord.http.Route.BASE',
        f'http://127.0.0.1:{server.server_port}/api/v10',
    )
    yield server
    server.shutdown()
    server.server_close()


@pytest.mark.parametrize(
    ('status', 'code', 'message', 'raised'),
    [
        # A webhook post to a deleted thread is a 400, not a 404.
        (400, 10003, 'Unknown Channel', transport.ThreadGone),
        (404, 10008, 'Unknown Message', transport.MessageGone),
        (404, 10015, 'Unknown Webhook', discord.NotFound),
    ],
)
def test_deleted_threads_and_messages_are_recognised(
    discord_api: FakeDiscord,
    status: int,
    code: int,
    message: str,
    raised: type[Exception],
) -> None:
    discord_api.answer = (status, code, message)

    async def main() -> None:
        link = discord_transport.DiscordTransport('token', 1, 2, {})
        async with aiohttp.ClientSession() as session:
            link.webhooks['triage'] = discord.Webhook.partial(
                3, 'secret', session=session
            )
            where = transport.Where('triage', 4)
            with pytest.raises(raised) as sent:
                await link.send(where, 'hi')
            with pytest.raises(raised) as edited:
                await link.edit(where, 5, 'hi')
        print(sent.value, edited.value)

    asyncio.run(main())
