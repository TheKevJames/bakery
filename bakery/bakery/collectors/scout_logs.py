"""
Scout's reading of CI logs: the warnings and masked failures in a passing
run's log archive.

Only lines the runner rendered count (`##[warning]…`, `##[error]…`), never a
raw `::warning::`/`::error::` workflow command: the runner prints each run
step's script before running it, so an `echo "::error::…"` in a script shows
up in the log whether or not it ever ran.
"""

import dataclasses
import io
import pathlib
import re
import zipfile
from collections.abc import Iterator

WARNING_RE = re.compile(
    r'DeprecationWarning|FutureWarning|PendingDeprecationWarning|##\[warning\]'
)
# A tool's error, or `Process completed with exit code N.` from a step that
# was allowed to fail: the run passed regardless.
ERROR_PREFIX = '##[error]'
LOG_TIMESTAMP_RE = re.compile(r'^\d{4}-\d\d-\d\dT[\d:.]+Z\s*')
STEP_PREFIX = '##[group]Run '
# `uses: owner/repo[/path]@ref`; local (`./…`) actions are the repo's own.
ACTION_STEP_RE = re.compile(r'^##\[group\]Run [\w.-]+/[\w./-]+@\S+$')
# Only actions have post steps, and the log does not name theirs.
POST_STEP = 'Post job cleanup.'
NODE_PID_RE = re.compile(r'^\(node:\d+\) ')
NODE_DEPRECATION_RE = re.compile(r'^\[DEP\d+\] ')
# Archive members are `<n>_<job>.txt`, or `<job>/<file>.txt`.
JOB_PREFIX_RE = re.compile(r'^\d+_')


@dataclasses.dataclass(frozen=True)
class Finding:
    kind: str  # 'warning' or 'error'
    message: str
    job: str
    # The step's `Run …` header (its command or action), as logs do not
    # name steps.
    step: str


def _job_findings(job: str, job_log: str) -> Iterator[Finding]:
    """
    Warnings and errors in a job's log, minus Node deprecations raised in
    actions.

    An action ships its dependencies bundled outside node_modules, so Node
    reports their deprecations, which only the action's maintainers can fix.
    Node prefixes its warnings with a pid, which differs every run.
    """
    in_action = False
    step = ''
    for raw in job_log.splitlines():
        line = LOG_TIMESTAMP_RE.sub('', raw).strip()
        if line.startswith(STEP_PREFIX) or line == POST_STEP:
            in_action = line == POST_STEP or bool(ACTION_STEP_RE.match(line))
            step = line.removeprefix(STEP_PREFIX)
        if line.startswith(ERROR_PREFIX):
            yield Finding('error', line, job, step)
            continue
        if not WARNING_RE.search(line):
            continue
        message = NODE_PID_RE.sub('', line)
        if in_action and NODE_DEPRECATION_RE.match(message):
            continue
        yield Finding('warning', message, job, step)


def findings(logs: bytes) -> Iterator[Finding]:
    """Every finding in a run's log archive, in job order."""
    with zipfile.ZipFile(io.BytesIO(logs)) as archive:
        for member in sorted(archive.namelist()):
            top = pathlib.PurePosixPath(member).parts[0]
            job = JOB_PREFIX_RE.sub('', top).removesuffix('.txt')
            yield from _job_findings(
                job, archive.read(member).decode(errors='replace')
            )
