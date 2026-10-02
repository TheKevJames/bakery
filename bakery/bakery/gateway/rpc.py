"""
A pi child process speaking pi's RPC protocol (`pi --mode rpc`).

Records are LF-delimited JSON on stdout. Command responses are matched to
their request by id; everything else (session events, extension UI requests)
is delivered in order through `next_event()`.
"""

import asyncio
import collections
import contextlib
import itertools
import json
import pathlib
from collections.abc import Mapping
from collections.abc import Sequence

# pi emits whole messages (eg. `agent_end`) as single records, so lines can be
# far larger than asyncio's 64KiB default.
LINE_LIMIT = 64 * 1024 * 1024
STDERR_TAIL_LINES = 50
CLOSE_TIMEOUT = 10.0

Record = dict[str, object]


class RpcError(Exception):
    pass


class ChildExited(RpcError):
    pass


class PiProcess:
    def __init__(self, process: 'asyncio.subprocess.Process') -> None:
        self.process = process
        self.events: asyncio.Queue[Record | None] = asyncio.Queue()
        self.stderr: collections.deque[str] = collections.deque(
            maxlen=STDERR_TAIL_LINES
        )
        self._pending: dict[str, asyncio.Future[Record]] = {}
        self._ids = itertools.count()
        self._tasks = [
            asyncio.create_task(self._read_stdout()),
            asyncio.create_task(self._read_stderr()),
        ]

    @classmethod
    async def start(
        cls, args: Sequence[str], *, cwd: pathlib.Path, env: Mapping[str, str]
    ) -> 'PiProcess':
        process = await asyncio.create_subprocess_exec(
            'pi',
            '--mode',
            'rpc',
            *args,
            cwd=cwd,
            # Deliberately not inherited: the caller decides exactly what a
            # claw may see.
            env=dict(env),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=LINE_LIMIT,
        )
        return cls(process)

    @property
    def alive(self) -> bool:
        return self.process.returncode is None

    def describe_exit(self) -> str:
        tail = '\n'.join(self.stderr)
        return f'pi exited ({self.process.returncode}): {tail}'.strip()

    async def _read_stdout(self) -> None:
        assert self.process.stdout
        while line := await self.process.stdout.readline():
            record: Record = json.loads(line)
            request_id = record.get('id')
            if record.get('type') == 'response' and request_id is not None:
                future = self._pending.pop(str(request_id), None)
                if future and not future.done():
                    future.set_result(record)
                    continue
            self.events.put_nowait(record)
        await self.process.wait()
        exited = ChildExited(self.describe_exit())
        for future in self._pending.values():
            if not future.done():
                future.set_exception(exited)
        self._pending.clear()
        self.events.put_nowait(None)

    async def _read_stderr(self) -> None:
        assert self.process.stderr
        while line := await self.process.stderr.readline():
            self.stderr.append(line.decode(errors='replace').rstrip())

    async def _write(self, record: Record) -> None:
        assert self.process.stdin
        if not self.alive:
            raise ChildExited(self.describe_exit())
        self.process.stdin.write(json.dumps(record).encode() + b'\n')
        await self.process.stdin.drain()

    async def command(self, record: Record) -> Record:
        """Send a command and return its response's `data`."""
        request_id = f'bakery-{next(self._ids)}'
        future: asyncio.Future[Record] = (
            asyncio.get_running_loop().create_future()
        )
        self._pending[request_id] = future
        await self._write({**record, 'id': request_id})
        response = await future
        if not response.get('success'):
            raise RpcError(f'{record["type"]} failed: {response.get("error")}')
        data = response.get('data')
        if not isinstance(data, dict):
            return {}
        return {str(key): value for key, value in data.items()}

    async def respond_ui(self, request_id: str, fields: Record) -> None:
        await self._write(
            {'type': 'extension_ui_response', 'id': request_id, **fields}
        )

    async def next_event(self) -> Record:
        record = await self.events.get()
        if record is None:
            self.events.put_nowait(None)
            raise ChildExited(self.describe_exit())
        return record

    def extension_errors(self) -> list[str]:
        """Drain buffered events, returning any extension errors."""
        errors = []
        while not self.events.empty():
            record = self.events.get_nowait()
            if record is None:
                self.events.put_nowait(None)
                break
            if record.get('type') == 'extension_error':
                errors.append(
                    f'extension error in {record.get("extensionPath")}'
                    f' ({record.get("event")}): {record.get("error")}'
                )
        return errors

    def drain_events(self) -> None:
        """Drop buffered events left over from a previous run."""
        while not self.events.empty():
            if self.events.get_nowait() is None:
                self.events.put_nowait(None)
                return

    async def close(self) -> None:
        """Close stdin (pi's orderly shutdown), killing pi if it hangs."""
        if self.process.stdin and not self.process.stdin.is_closing():
            self.process.stdin.close()
        try:
            await asyncio.wait_for(self.process.wait(), CLOSE_TIMEOUT)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                self.process.kill()
            await self.process.wait()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
