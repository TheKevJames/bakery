"""
The seam between the relay (what to say, and when) and Discord (how).

A Transport delivers messages; a Handler receives what I do in Discord.
discord_transport.py is the real Transport; tests use a fake one.
"""

import dataclasses
from collections.abc import Sequence
from typing import Literal
from typing import Protocol

Style = Literal['primary', 'secondary', 'success', 'danger']


@dataclasses.dataclass(frozen=True)
class Button:
    custom_id: str
    label: str
    style: Style = 'secondary'


@dataclasses.dataclass(frozen=True)
class Select:
    """Option values are their indices, as strings."""

    custom_id: str
    options: tuple[str, ...]
    placeholder: str = ''


Component = Button | Select


@dataclasses.dataclass(frozen=True)
class Attachment:
    filename: str
    data: bytes


@dataclasses.dataclass(frozen=True)
class Where:
    """A claw's thread (posted as that claw), or #bakery when claw is None."""

    claw: str | None
    thread_id: int | None = None


@dataclasses.dataclass(frozen=True)
class Response:
    text: str
    private: bool = False


class Handler(Protocol):
    async def on_message(
        self,
        author_id: int,
        claw: str,
        thread_id: int | None,
        message_id: int,
        text: str,
    ) -> None:
        """A message in a claw's channel (thread_id None) or thread."""

    async def on_component(
        self, author_id: int, custom_id: str, value: str | None
    ) -> str:
        """A button or select; returns a private reply. Must be quick."""
        ...

    async def on_command(
        self, author_id: int, name: str, options: dict[str, object]
    ) -> Response: ...


class Transport(Protocol):
    async def start(self, handler: Handler, claws: Sequence[str]) -> None:
        """Connect and ensure each claw has a channel and webhook."""

    async def close(self) -> None: ...

    async def create_thread(
        self, claw: str, name: str, from_message: int | None = None
    ) -> int: ...

    async def send(
        self,
        where: Where,
        text: str,
        *,
        components: Sequence[Component] = (),
        attachment: Attachment | None = None,
    ) -> int:
        """Returns the message id. Only the owner can be mentioned."""
        ...

    async def edit(
        self,
        where: Where,
        message_id: int,
        text: str,
        *,
        components: Sequence[Component] | None = None,
    ) -> None:
        """`components=None` keeps the message's components."""

    async def react(self, where: Where, message_id: int, emoji: str) -> None:
        """Acknowledge one of my messages."""
