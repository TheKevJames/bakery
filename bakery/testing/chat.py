"""A fake Discord transport, recording what the relay posts."""

import asyncio
import dataclasses
import itertools
from collections.abc import Callable
from collections.abc import Sequence

from bakery.chat import transport

WAIT_TIMEOUT = 60.0
OWNER = 1001
STRANGER = 2002


@dataclasses.dataclass
class Message:
    where: transport.Where
    text: str
    components: tuple[transport.Component, ...]
    attachment: transport.Attachment | None
    reactions: list[str] = dataclasses.field(default_factory=list)

    def button(self, label: str) -> transport.Button:
        for component in self.components:
            if (
                isinstance(component, transport.Button)
                and component.label == label
            ):
                return component
        raise AssertionError(f'no {label!r} button on {self.text!r}')


@dataclasses.dataclass
class Thread:
    claw: str
    name: str
    from_message: int | None


class FakeTransport:
    def __init__(self) -> None:
        self.ids = itertools.count(1)
        self.threads: dict[int, Thread] = {}
        self.messages: dict[int, Message] = {}
        self.changed = asyncio.Event()

    def _new_id(self) -> int:
        return next(self.ids)

    async def start(
        self, handler: transport.Handler, claws: Sequence[str]
    ) -> None:
        del handler, claws

    async def close(self) -> None:
        pass

    async def create_thread(
        self, claw: str, name: str, from_message: int | None = None
    ) -> int:
        thread_id = self._new_id()
        self.threads[thread_id] = Thread(claw, name, from_message)
        return thread_id

    async def send(
        self,
        where: transport.Where,
        text: str,
        *,
        components: Sequence[transport.Component] = (),
        attachment: transport.Attachment | None = None,
    ) -> int:
        assert len(text) <= 2000, len(text)
        message_id = self._new_id()
        self.messages[message_id] = Message(
            where, text, tuple(components), attachment
        )
        self.changed.set()
        return message_id

    async def edit(
        self,
        where: transport.Where,
        message_id: int,
        text: str,
        *,
        components: Sequence[transport.Component] | None = None,
    ) -> None:
        assert len(text) <= 2000, len(text)
        message = self.messages[message_id]
        assert message.where == where
        message.text = text
        if components is not None:
            message.components = tuple(components)
        self.changed.set()

    async def react(
        self, where: transport.Where, message_id: int, emoji: str
    ) -> None:
        del where
        self.messages.setdefault(
            message_id, Message(transport.Where(None), '<mine>', (), None)
        ).reactions.append(emoji)
        self.changed.set()

    # Test helpers

    def thread_named(self, name: str) -> int:
        matches = [i for i, th in self.threads.items() if th.name == name]
        assert len(matches) == 1, (name, self.threads)
        return matches[0]

    def in_thread(self, thread_id: int) -> list[Message]:
        return [
            m for m in self.messages.values() if m.where.thread_id == thread_id
        ]

    def notices(self) -> list[str]:
        return [m.text for m in self.messages.values() if m.where.claw is None]

    async def until(self, predicate: Callable[[], object]) -> None:
        """Wait until `predicate()` is truthy after some transport change."""
        async with asyncio.timeout(WAIT_TIMEOUT):
            while not predicate():
                self.changed.clear()
                await self.changed.wait()

    async def message(self, thread_id: int, prefix: str) -> Message:
        """The first message in a thread starting with `prefix`."""

        def find() -> Message | None:
            return next(
                (
                    m
                    for m in self.in_thread(thread_id)
                    if m.text.startswith(prefix)
                ),
                None,
            )

        await self.until(find)
        found = find()
        assert found is not None
        return found
