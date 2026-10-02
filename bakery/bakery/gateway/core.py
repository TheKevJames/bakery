"""
The gateway: queues work for claws and runs it within concurrency and budget
limits.

Every request to run is a unit-of-work key plus a prompt. A key's first run
is "new" work; later runs of the same key are "resumes", which may use a
claw's extra resume slots. Runs of one key never overlap.
"""

import asyncio
import contextlib
import dataclasses
import datetime
import logging
import pathlib
import zoneinfo

from .. import collectors
from .. import state
from . import channel as channel_
from . import config
from . import ledger
from . import pool
from . import rpc
from . import runner
from . import scheduler

RESUME_PROMPT = 'Continue where you left off.'
SHUTDOWN_GRACE = 30.0

log = logging.getLogger(__name__)


class GatewayError(Exception):
    """A rejected control request."""


@dataclasses.dataclass(frozen=True)
class Request:
    claw: str
    work_key: str
    prompt: str
    trigger: str
    # The job that started the unit of work; its later runs inherit it.
    job: str | None = None


@dataclasses.dataclass
class Active:
    request: Request
    slot: str  # 'new' | 'resume'
    task: asyncio.Task[None] | None = None
    info: channel_.RunInfo | None = None
    # Set once the run owns a child with a clean event queue.
    proc: rpc.PiProcess | None = None
    # Set by pause --abort before the child exists; applied once it does.
    abort: tuple[ledger.Status, str] | None = None


def _stamp() -> str:
    return datetime.datetime.now().strftime('%Y%m%d-%H%M%S')


class Gateway:
    # pylint: disable=too-many-instance-attributes
    def __init__(
        self, claws_dir: pathlib.Path, channel: channel_.Channel
    ) -> None:
        self.claws_dir = claws_dir
        self.channel = channel
        self.config = config.load(claws_dir)
        self.runs = ledger.Ledger(state.root() / '_gateway' / 'runs.db')
        self.pool = pool.Pool()
        self.scheduler = scheduler.Scheduler(self._fire)
        self.queue: list[Request] = []
        self.active: dict[str, Active] = {}
        self.paused: set[str] = set()
        self._wake = asyncio.Event()
        self._dispatcher: asyncio.Task[None] | None = None
        self._notices: set[asyncio.Task[None]] = set()
        self._collections: dict[tuple[str, str], asyncio.Task[None]] = {}
        # (day, claw) pairs already announced as out of budget.
        self._broke: set[tuple[datetime.date, str]] = set()

    @property
    def tz(self) -> zoneinfo.ZoneInfo:
        return self.config.gateway.timezone

    async def start(self) -> None:
        await asyncio.to_thread(state.init)
        interrupted = self.runs.mark_interrupted()
        if interrupted:
            log.warning('marked %d unfinished runs interrupted', interrupted)
        self.pool.start()
        self.scheduler.start(self.config.claws.values(), self.tz)
        self._dispatcher = asyncio.create_task(self._dispatch_loop())
        self._notify('gateway started')

    async def stop(self) -> None:
        self.scheduler.stop()
        for task in list(self._collections.values()):
            task.cancel()
        await self._notice('gateway stopping')
        if self._dispatcher:
            self._dispatcher.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._dispatcher
        for active in self.active.values():
            self._abort(active, ledger.Status.interrupted, 'gateway stopped')
        tasks = [a.task for a in self.active.values() if a.task]
        if tasks:
            await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE)
        await self.pool.close()
        self.runs.mark_interrupted()
        self.runs.close()

    async def _notice(self, text: str) -> None:
        try:
            await self.channel.notice(text)
        except Exception:
            log.exception('channel failed posting notice: %s', text)

    def _notify(self, text: str) -> None:
        log.info('notice: %s', text)
        task = asyncio.create_task(self._notice(text))
        self._notices.add(task)
        task.add_done_callback(self._notices.discard)

    # Dispatch

    def submit(self, request: Request) -> None:
        self.config.claw(request.claw)
        self.queue.append(request)
        self._wake.set()

    def _fire(
        self, claw: config.Claw, job: config.Job, due: datetime.datetime
    ) -> None:
        self._start_job(
            claw,
            job,
            Request(
                claw.name,
                scheduler.work_key(claw, job, due),
                job.prompt,
                trigger=f'cron:{job.name}',
                job=job.name,
            ),
            manual=False,
        )

    def _start_job(
        self,
        claw: config.Claw,
        job: config.Job,
        request: Request,
        *,
        manual: bool,
    ) -> None:
        if job.collector is None:
            self.submit(request)
            return
        key = (claw.name, job.name)
        busy = [
            r
            for r in (*self.queue, *(a.request for a in self.active.values()))
            if (r.claw, r.job) == key
        ]
        # A second collection would hand over the same candidates again.
        if key in self._collections or busy:
            if manual:
                self._notify(f'{claw.name}/{job.name} is already running')
            return
        task = asyncio.create_task(self._collect(claw, job, request, manual))
        self._collections[key] = task
        task.add_done_callback(lambda _: self._collections.pop(key, None))

    async def _collect(
        self,
        claw: config.Claw,
        job: config.Job,
        request: Request,
        manual: bool,
    ) -> None:
        assert job.collector is not None
        collector = collectors.COLLECTORS[job.collector]
        try:
            collection = await asyncio.to_thread(collector, claw)
        except Exception as e:
            log.exception('collecting for %s/%s failed', claw.name, job.name)
            self._notify(f'{claw.name}/{job.name}: collecting failed: {e}')
            return
        if collection.errors:
            self._notify(
                f'{claw.name}/{job.name} collection problems:\n'
                + '\n'.join(collection.errors)
            )
        if collection.text is None:
            log.info('%s/%s: nothing new', claw.name, job.name)
            if manual:
                self._notify(f'{claw.name}/{job.name}: nothing new')
            return
        prompt = f'{request.prompt}\n\n{collection.text}'
        self.submit(dataclasses.replace(request, prompt=prompt))

    async def _dispatch_loop(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            waiting = []
            for request in self.queue:
                slot = self._slot(request)
                if slot is None:
                    waiting.append(request)
                else:
                    self._start(request, slot)
            self.queue = waiting

    def _slot(self, request: Request) -> str | None:
        """The slot `request` can start in now, if any."""
        claw = self.config.claws.get(request.claw)
        if claw is None or not claw.enabled:
            return None
        if {'all', request.claw} & self.paused:
            return None
        if request.work_key in self.active:
            return None
        if len(self.active) >= self.config.gateway.max_processes:
            return None
        slot = self._claw_slot(
            claw, resume=self.runs.has_runs(request.work_key)
        )
        if slot is None:
            return None
        needs_process = not self.pool.has(request.work_key)
        if needs_process and len(self.pool) >= (
            self.config.gateway.max_processes
        ):
            # Busy children are capped above, so one must be idle.
            self.pool.evict_idle()
        return slot

    def _claw_slot(self, claw: config.Claw, *, resume: bool) -> str | None:
        """New work takes a new slot; a resume prefers a resume slot."""
        mine = [a for a in self.active.values() if a.request.claw == claw.name]
        new = sum(a.slot == 'new' for a in mine)
        resumes = len(mine) - new
        if resume and resumes < claw.concurrency.max_resumes:
            return 'resume'
        if new < claw.concurrency.max_active:
            return 'new'
        return None

    def _start(self, request: Request, slot: str) -> None:
        active = Active(request, slot)
        self.active[request.work_key] = active
        active.task = asyncio.create_task(self._run(active))

    def _budget(self, claw: config.Claw) -> runner.Budget:
        claw_left = claw.limits.daily_cost_usd - self.runs.spent_today(
            self.tz, claw.name
        )
        global_left = self.config.gateway.daily_cost_usd - (
            self.runs.spent_today(self.tz)
        )
        return runner.Budget(
            cost_usd=min(claw.limits.cost_usd, claw_left, global_left),
            turns=claw.limits.turns,
            seconds=claw.limits.minutes * 60,
        )

    async def _run(self, active: Active) -> None:
        request = active.request
        claw = self.config.claw(request.claw)
        budget = self._budget(claw)
        job_name = request.job or self.runs.last_job(request.work_key)
        job = next((j for j in claw.jobs if j.name == job_name), None)
        info = channel_.RunInfo(
            run_id=self.runs.start(
                claw.name, request.work_key, request.trigger, job_name
            ),
            claw=claw.name,
            work_key=request.work_key,
            session_id=pool.session_id(request.work_key),
            trigger=request.trigger,
            silent_ok=job is not None and job.silent_ok,
        )
        active.info = info
        try:
            if budget.cost_usd <= 0:
                info.status = ledger.Status.parked
                info.reason = 'daily budget exhausted'
                self._announce_broke(claw.name)
                return
            proc = await self.pool.acquire(claw, request.work_key, job)
            # Events left over from this child's previous run must go before
            # any abort is queued, or the abort would be dropped with them.
            proc.drain_events()
            active.proc = proc
            if active.abort is not None:
                runner.request_abort(proc, *active.abort)
            await self.channel.run_started(info)
            await runner.execute(
                proc, info, request.prompt, budget, self.channel, self.runs
            )
        except Exception as e:
            log.exception('run %s failed', request.work_key)
            info.status, info.reason = ledger.Status.failed, str(e)
        finally:
            await self._finish(info)

    async def _finish(self, info: channel_.RunInfo) -> None:
        self.runs.record(info.run_id, cost_usd=info.cost_usd, turns=info.turns)
        self.runs.finish(info.run_id, info.status, info.reason)
        self.pool.release(info.work_key)
        del self.active[info.work_key]
        self._wake.set()
        log.info(
            'run %s %s%s',
            info.work_key,
            info.status,
            f': {info.reason}' if info.reason else '',
        )
        try:
            await self.channel.run_finished(info)
        except Exception:
            log.exception('channel failed reporting %s', info.work_key)
        try:
            await asyncio.to_thread(
                state.commit, f'{info.work_key}: {info.status}'
            )
        except Exception:
            log.exception('committing state after %s failed', info.work_key)

    def _announce_broke(self, claw: str) -> None:
        key = (datetime.datetime.now(self.tz).date(), claw)
        if key not in self._broke:
            self._broke.add(key)
            self._notify(f'{claw} has exhausted its daily budget')

    def _abort(
        self, active: Active, status: ledger.Status, reason: str
    ) -> None:
        active.abort = (status, reason)
        if active.proc is not None:
            runner.request_abort(active.proc, status, reason)

    # Control operations

    def _target(self, target: str) -> str:
        if target != 'all':
            self.config.claw(target)
        return target

    def trigger(
        self, claw_name: str, job_name: str | None, prompt: str | None
    ) -> str:
        claw = self.config.claw(claw_name)
        if not claw.enabled:
            raise GatewayError(f'{claw.name} is disabled')
        if prompt is not None and job_name is None:
            work_key = f'{claw.name}/manual-{_stamp()}'
            self.submit(Request(claw.name, work_key, prompt, 'manual'))
            return work_key
        if job_name is None:
            # Shared jobs (eg. dream) must be named.
            own = [job for job in claw.jobs if not job.shared]
            if len(own) != 1:
                raise GatewayError(f'{claw.name}: name a job or a --prompt')
            job_name = own[0].name
        job = claw.job(job_name)
        work_key = f'{claw.name}/{job.name}-{_stamp()}'
        self._start_job(
            claw,
            job,
            Request(
                claw.name,
                work_key,
                prompt or job.prompt,
                f'manual:{job.name}',
                job.name,
            ),
            manual=True,
        )
        return work_key

    def pause(self, target: str, *, abort: bool) -> None:
        self.paused.add(self._target(target))
        if abort:
            for active in self.active.values():
                if target in ('all', active.request.claw):
                    self._abort(active, ledger.Status.parked, 'paused')

    def resume(self, target: str) -> None:
        """Unpause a claw (or all), or re-run a stopped unit of work."""
        if '/' not in target:
            if target == 'all':
                self.paused.clear()
            else:
                self.paused.discard(self._target(target))
            self._wake.set()
            return
        claw = self.runs.last_claw(target)
        if claw is None:
            raise GatewayError(f'no runs for {target}')
        if target in self.active or any(
            r.work_key == target for r in self.queue
        ):
            raise GatewayError(f'{target} is already running or queued')
        self.submit(Request(claw, target, RESUME_PROMPT, 'resume'))

    def message(self, claw: str, work_key: str, text: str) -> str:
        """
        Deliver a message from me to a unit of work.

        Steers the unit's run if one is in flight, else queues a run with the
        message as its prompt. Returns 'steered' or 'queued'.
        """
        if not self.config.claw(claw).enabled:
            raise GatewayError(f'{claw} is disabled')
        active = self.active.get(work_key)
        if active is not None and active.proc is not None:
            runner.request_steer(active.proc, text)
            return 'steered'
        self.submit(Request(claw, work_key, text, 'message'))
        return 'queued'

    def reload(self) -> None:
        try:
            self.config = config.load(self.claws_dir)
        except config.ConfigError as e:
            self._notify(f'config reload rejected: {e}')
            raise
        self.scheduler.start(self.config.claws.values(), self.tz)
        self._wake.set()

    def status(self) -> dict[str, object]:
        claws = {}
        for claw in self.config.claws.values():
            claws[claw.name] = {
                'enabled': claw.enabled,
                'paused': bool({'all', claw.name} & self.paused),
                'active': [
                    dataclasses.asdict(a.info)
                    for a in self.active.values()
                    if a.request.claw == claw.name and a.info is not None
                ],
                'queued': [
                    r.work_key for r in self.queue if r.claw == claw.name
                ],
                'next': {
                    job: due.isoformat()
                    for (name, job), due in self.scheduler.upcoming.items()
                    if name == claw.name
                },
            }
        return {'processes': len(self.pool), 'claws': claws}

    def budget(self) -> dict[str, object]:
        gateway = self.config.gateway
        return {
            'global': {
                'spent_usd': self.runs.spent_today(self.tz),
                'limit_usd': gateway.daily_cost_usd,
            },
            'claws': {
                claw.name: {
                    'spent_usd': self.runs.spent_today(self.tz, claw.name),
                    'limit_usd': claw.limits.daily_cost_usd,
                }
                for claw in self.config.claws.values()
            },
        }
