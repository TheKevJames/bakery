"""
Collectors: deterministic Python that gathers a job's input before its run.

A job naming a `collector` only runs when the collector finds something,
unless its `skip_empty` is false; its text is appended to the job's prompt.
Collectors run in a worker thread.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import baker
from . import base
from . import changes
from . import scout
from . import scout_repos
from . import triage

if TYPE_CHECKING:
    from ..gateway import config

Collector = Callable[['config.Claw'], base.Collection]
# Problems with a claw's collector settings that only the outside world can
# show (eg. a repo override the claws cannot reach); `bakery gateway check`
# reports them.
Check = Callable[['config.Claw'], list[str]]

COLLECTORS: dict[str, Collector] = {
    'baker': baker.collect,
    'changes': changes.collect,
    'scout': scout.collect,
    'triage': triage.collect,
    'triage-recheck': triage.recheck,
    'triage-unblock': triage.unblock,
}

CHECKS: dict[str, Check] = {'scout': scout_repos.check}
