"""
The run ledger: one row per run (one prompt until pi settles).

A unit of work (eg. one build ticket) spans many runs, linked by work key.
Spend is summed from here to enforce daily budgets, which reset at local
midnight in the gateway's timezone.
"""

import datetime
import enum
import pathlib
import sqlite3
import time
import zoneinfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    claw TEXT NOT NULL,
    work_key TEXT NOT NULL,
    trigger TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    status TEXT NOT NULL,
    reason TEXT,
    cost_usd REAL NOT NULL DEFAULT 0,
    turns INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS runs_work_key ON runs (work_key);
CREATE INDEX IF NOT EXISTS runs_started_at ON runs (started_at);
CREATE TABLE IF NOT EXISTS threads (
    work_key TEXT PRIMARY KEY,
    thread_id INTEGER NOT NULL UNIQUE
);
"""


class Status(enum.StrEnum):
    running = 'running'
    settled = 'settled'
    # Stopped by a limit or a pause; resumable.
    parked = 'parked'
    failed = 'failed'
    # The gateway stopped while the run was in flight.
    interrupted = 'interrupted'


class Ledger:
    def __init__(self, path: pathlib.Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def close(self) -> None:
        self.db.close()

    def start(self, claw: str, work_key: str, trigger: str) -> int:
        cursor = self.db.execute(
            'INSERT INTO runs (claw, work_key, trigger, started_at, status)'
            ' VALUES (?, ?, ?, ?, ?)',
            (claw, work_key, trigger, time.time(), Status.running),
        )
        assert cursor.lastrowid is not None
        return cursor.lastrowid

    def record(self, run_id: int, *, cost_usd: float, turns: int) -> None:
        """Persist progress, so a crash still counts spend already made."""
        self.db.execute(
            'UPDATE runs SET cost_usd = ?, turns = ? WHERE id = ?',
            (cost_usd, turns, run_id),
        )

    def finish(
        self, run_id: int, status: Status, reason: str | None = None
    ) -> None:
        self.db.execute(
            'UPDATE runs SET status = ?, reason = ?, ended_at = ?'
            ' WHERE id = ?',
            (status, reason, time.time(), run_id),
        )

    def mark_interrupted(self) -> int:
        cursor = self.db.execute(
            'UPDATE runs SET status = ?, reason = ?, ended_at = ?'
            ' WHERE status = ?',
            (
                Status.interrupted,
                'gateway stopped',
                time.time(),
                Status.running,
            ),
        )
        return cursor.rowcount

    def has_runs(self, work_key: str) -> bool:
        row = self.db.execute(
            'SELECT 1 FROM runs WHERE work_key = ? LIMIT 1', (work_key,)
        ).fetchone()
        return row is not None

    def last_claw(self, work_key: str) -> str | None:
        row = self.db.execute(
            'SELECT claw FROM runs WHERE work_key = ? ORDER BY id DESC',
            (work_key,),
        ).fetchone()
        return None if row is None else str(row['claw'])

    def work_key_of(self, run_id: int) -> str | None:
        row = self.db.execute(
            'SELECT work_key FROM runs WHERE id = ?', (run_id,)
        ).fetchone()
        return None if row is None else str(row['work_key'])

    def set_thread(self, work_key: str, thread_id: int) -> None:
        self.db.execute(
            'INSERT OR REPLACE INTO threads (work_key, thread_id)'
            ' VALUES (?, ?)',
            (work_key, thread_id),
        )

    def thread_of(self, work_key: str) -> int | None:
        row = self.db.execute(
            'SELECT thread_id FROM threads WHERE work_key = ?', (work_key,)
        ).fetchone()
        return None if row is None else int(row['thread_id'])

    def work_key_of_thread(self, thread_id: int) -> str | None:
        row = self.db.execute(
            'SELECT work_key FROM threads WHERE thread_id = ?', (thread_id,)
        ).fetchone()
        return None if row is None else str(row['work_key'])

    def spent_today(
        self, tz: zoneinfo.ZoneInfo, claw: str | None = None
    ) -> float:
        midnight = datetime.datetime.now(tz).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        query = (
            'SELECT COALESCE(SUM(cost_usd), 0) FROM runs WHERE started_at >= ?'
        )
        params: tuple[object, ...] = (midnight.timestamp(),)
        if claw is not None:
            query += ' AND claw = ?'
            params += (claw,)
        total: float = self.db.execute(query, params).fetchone()[0]
        return total
