"""
Collectors: deterministic Python that gathers a job's input before its run.

A job naming a `collector` only runs when the collector finds something; its
text is appended to the job's prompt. Collectors run in a worker thread.
"""

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import base
from . import scout
from . import triage

if TYPE_CHECKING:
    from ..gateway import config

Collector = Callable[['config.Claw'], base.Collection]

COLLECTORS: dict[str, Collector] = {
    'scout': scout.collect,
    'triage': triage.collect,
}
