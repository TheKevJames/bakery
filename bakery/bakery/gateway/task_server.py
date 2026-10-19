"""
Serving each run's task list commands.

A claw cannot write the task list itself: while a run is in flight, the
gateway runs its `task` commands for it on a unix socket only that unit's
sandbox can reach (`state.task_socket`), as the unit, under its claw's rules
(see `task_list.claims`). The socket's path is the unit's identity, so
nothing the claw sends can change who it is. When the run ends, so do the
server and the unit's claims.
"""

import asyncio
import logging

from task import cli

from .. import state
from ..task_list import claims
from . import config
from . import pool

log = logging.getLogger(__name__)


async def serve(claw: config.Claw, work_key: str) -> asyncio.Server:
    path = state.task_socket(pool.session_id(work_key))
    path.parent.mkdir(parents=True, exist_ok=True)
    # Left by a gateway which stopped without closing it.
    path.unlink(missing_ok=True)
    return await cli.listen(path, claims.writer(claw.rules, work_key))


async def close(server: asyncio.Server | None, work_key: str) -> None:
    """
    Stop serving the unit, if it was served, then release whatever it still
    claims: its collector claims for it before it starts, if it does.
    """
    if server is not None:
        server.close()
        await server.wait_closed()
    released = await asyncio.to_thread(claims.release, work_key)
    if released:
        log.info('%s released %s', work_key, ', '.join(released))
