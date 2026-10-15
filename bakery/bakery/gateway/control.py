"""
The gateway control socket: one JSON request per line, one JSON response per
line. Used by the `bakery` CLI; Discord slash commands call the same Gateway
operations in-process.
"""

import asyncio
import contextlib
import inspect
import json
import pathlib
import socket
from collections.abc import Awaitable
from collections.abc import Callable
from collections.abc import Mapping

from .. import state
from . import config
from . import core
from . import teach

# Returns the response's data, or an awaitable of it (eg. to wait on a run).
Handler = Callable[[dict[str, object]], object]


def socket_path() -> pathlib.Path:
    return state.root() / '_gateway' / 'gateway.sock'


class ControlError(Exception):
    pass


def _str(request: dict[str, object], key: str) -> str | None:
    value = request.get(key)
    if value is not None and not isinstance(value, str):
        raise core.GatewayError(f'{key} must be a string')
    return value


def _required(request: dict[str, object], key: str) -> str:
    value = _str(request, key)
    if value is None:
        raise core.GatewayError(f'missing {key}')
    return value


def _trigger(
    gateway: core.Gateway, request: dict[str, object]
) -> dict[str, object]:
    work_key = gateway.trigger(
        _required(request, 'claw'),
        _str(request, 'job'),
        _str(request, 'prompt'),
    )
    return {'work_key': work_key, 'collecting': gateway.collecting(work_key)}


def handlers(gateway: core.Gateway) -> dict[str, Handler]:
    teacher = teach.Teacher(gateway)
    return {
        'trigger': lambda r: _trigger(gateway, r),
        'teach': lambda r: teacher.start(
            _required(r, 'target'),
            _required(r, 'lesson'),
            wait=bool(r.get('wait')),
        ),
        'teach_result': lambda r: teacher.result(_required(r, 'work_key')),
        'pause': lambda r: gateway.pause(
            _required(r, 'target'), abort=bool(r.get('abort'))
        ),
        'resume': lambda r: gateway.resume(_required(r, 'target')),
        'reload': lambda r: gateway.reload(),
        'status': lambda r: gateway.status(),
        'budget': lambda r: gateway.budget(),
    }


async def _respond(
    table: dict[str, Handler], line: bytes
) -> dict[str, object]:
    try:
        request = json.loads(line)
        if not isinstance(request, dict):
            raise core.GatewayError('request must be an object')
        handler = table.get(str(request.get('type')))
        if handler is None:
            raise core.GatewayError(f'unknown request: {request.get("type")}')
        data = handler(request)
        if inspect.isawaitable(data):
            data = await data
    except (ValueError, core.GatewayError, config.ConfigError) as e:
        return {'ok': False, 'error': str(e)}
    return {'ok': True, 'data': data}


async def serve(gateway: core.Gateway) -> Callable[[], Awaitable[None]]:
    """Start serving; returns a coroutine function that stops the server."""
    path = socket_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    table = handlers(gateway)
    connections: set[asyncio.Task[None]] = set()

    async def connection(
        reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        task = asyncio.current_task()
        assert task is not None
        connections.add(task)
        try:
            with contextlib.closing(writer):
                while line := await reader.readline():
                    response = await _respond(table, line)
                    writer.write(json.dumps(response).encode() + b'\n')
                    await writer.drain()
        except ConnectionError:
            pass  # The client left, eg. a CLI that stopped waiting.
        finally:
            connections.discard(task)

    server = await asyncio.start_unix_server(connection, path)
    path.chmod(0o600)

    async def stop() -> None:
        server.close()
        # A request may be waiting on a run that will now never start, and
        # wait_closed waits for every connection.
        for task in list(connections):
            task.cancel()
        await server.wait_closed()
        path.unlink(missing_ok=True)

    return stop


def send(record: Mapping[str, object], timeout: float | None = 10.0) -> object:
    """
    Send one request to the running gateway and return its data; `timeout`
    None waits as long as the request does (eg. on a run).
    """
    buffer = b''
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        try:
            sock.connect(str(socket_path()))
        except OSError as e:
            raise ControlError(f'gateway is not running ({e})') from None
        sock.sendall(json.dumps(record).encode() + b'\n')
        while b'\n' not in buffer:
            chunk = sock.recv(65536)
            if not chunk:
                break
            buffer += chunk
    response = json.loads(buffer.split(b'\n', 1)[0] or b'{}')
    if not response.get('ok'):
        raise ControlError(response.get('error', 'no response from gateway'))
    return response.get('data')
