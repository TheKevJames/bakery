import asyncio
import http.server
import json
import threading
import types
from collections.abc import Iterator
from typing import Any
from typing import cast

import aiohttp
import discord
import pytest
from discord.types import user as user_types

from bakery.chat import discord_transport
from bakery.chat import transport
from testing import servers

BOT = 99


class FakeDiscord(servers.LocalServer):
    """
    Discord's API: webhooks in `deleted` answer Unknown Webhook, others
    `answer` (a JSON error), or post the message if it is None.
    """

    answer: tuple[int, int, str] | None = None

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[http.server.BaseHTTPRequestHandler],
    ) -> None:
        super().__init__(address, handler)
        self.deleted: set[int] = set()
        self.requests: list[tuple[int, bytes]] = []  # webhook id, body


class Handler(http.server.BaseHTTPRequestHandler):
    def _answer(self) -> None:
        assert isinstance(self.server, FakeDiscord)
        body = self.rfile.read(int(self.headers.get('Content-Length', 0)))
        # /api/v10/webhooks/<id>/<token>[/messages/<id>]
        hook = int(self.path.split('?')[0].split('/')[4])
        self.server.requests.append((hook, body))
        if hook in self.server.deleted:
            self._json(404, {'code': 10015, 'message': 'Unknown Webhook'})
        elif self.server.answer is not None:
            status, code, message = self.server.answer
            self._json(status, {'code': code, 'message': message})
        else:
            self._json(200, _message(hook))

    def _json(self, status: int, data: dict[str, Any]) -> None:
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_POST = do_PATCH = _answer

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


def _user(user: int) -> user_types.User:
    return {
        'id': str(user),
        'username': 'bakery',
        'discriminator': '0',
        'avatar': None,
        'global_name': None,
    }


def _message(hook: int) -> dict[str, Any]:
    """A webhook's message, with the webhook's id as its own."""
    return {
        'id': str(hook),
        'channel_id': '4',
        'type': 0,
        'content': 'hi',
        'author': _user(hook),
        'attachments': [],
        'embeds': [],
    }


def _webhook(
    session: aiohttp.ClientSession, hook: int, owner: int = BOT
) -> discord.Webhook:
    return discord.Webhook(
        {'id': str(hook), 'type': 1, 'token': 'secret', 'user': _user(owner)},
        session=session,
    )


class Channel:
    """A claw's channel: lists its webhooks, and creates one."""

    def __init__(
        self, session: aiohttp.ClientSession, hooks: list[discord.Webhook]
    ) -> None:
        self.session = session
        self.hooks = hooks
        self.created = 0

    async def webhooks(self) -> list[discord.Webhook]:
        await asyncio.sleep(0)  # let concurrent reports race
        return self.hooks.copy()

    async def create_webhook(
        self, *, name: str, avatar: bytes | None
    ) -> discord.Webhook:
        del name, avatar
        self.created += 1
        hook = _webhook(self.session, 100 + self.created)
        self.hooks.append(hook)
        return hook


def _link(
    session: aiohttp.ClientSession, channel: Channel
) -> discord_transport.DiscordTransport:
    """A transport whose triage webhook (3) is gone from `channel`."""
    link = discord_transport.DiscordTransport('token', 1, 2, {})
    # The bot's user, as Discord announces it on connecting.
    link.client._connection.user = cast(  # pylint: disable=protected-access
        'discord.ClientUser', types.SimpleNamespace(id=BOT)
    )
    link.channels['triage'] = cast('discord.TextChannel', channel)
    link.webhooks['triage'] = _webhook(session, 3)
    return link


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
        (404, 10004, 'Unknown Guild', discord.NotFound),
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


def test_a_deleted_webhook_is_created_again_and_the_send_retried(
    discord_api: FakeDiscord,
) -> None:
    discord_api.deleted.add(3)

    async def main() -> None:
        async with aiohttp.ClientSession() as session:
            channel = Channel(session, [])
            link = _link(session, channel)
            where = transport.Where('triage', 4)
            attachment = transport.Attachment('reply.md', b'the reply')
            assert await link.send(where, 'hi', attachment=attachment) == 101
            assert await link.send(where, 'again') == 101
            assert channel.created == 1
            assert link.webhooks['triage'].id == 101

    asyncio.run(main())
    assert [hook for hook, _ in discord_api.requests] == [3, 101, 101]
    # The retry carried the attachment the first attempt read.
    assert b'the reply' in discord_api.requests[1][1]


def test_concurrent_reports_renew_a_deleted_webhook_once(
    discord_api: FakeDiscord,
) -> None:
    discord_api.deleted.add(3)

    async def main() -> None:
        async with aiohttp.ClientSession() as session:
            channel = Channel(session, [])
            link = _link(session, channel)
            where = transport.Where('triage', 4)
            sent = await asyncio.gather(
                *(link.send(where, f'hi {i}') for i in range(3))
            )
            assert sent == [101, 101, 101]
            assert channel.created == 1

    asyncio.run(main())


def test_an_existing_bot_webhook_is_reused_in_place_of_a_deleted_one(
    discord_api: FakeDiscord,
) -> None:
    discord_api.deleted.add(3)

    async def main() -> None:
        async with aiohttp.ClientSession() as session:
            others = _webhook(session, 7, owner=BOT + 1)
            ours = _webhook(session, 8)
            channel = Channel(session, [others, ours])
            link = _link(session, channel)
            assert await link.send(transport.Where('triage', 4), 'hi') == 8
            assert channel.created == 0

    asyncio.run(main())


def test_an_edit_through_a_deleted_webhook_renews_it_but_cannot_succeed(
    discord_api: FakeDiscord,
) -> None:
    discord_api.deleted.add(3)

    async def main() -> None:
        async with aiohttp.ClientSession() as session:
            channel = Channel(session, [])
            link = _link(session, channel)
            where = transport.Where('triage', 4)
            # Only the webhook that posted a message can edit it, so the
            # relay posts it again instead.
            with pytest.raises(transport.MessageGone):
                await link.edit(where, 5, 'hi')
            assert link.webhooks['triage'].id == 101
            assert await link.send(where, 'hi') == 101
            assert channel.created == 1

    asyncio.run(main())
