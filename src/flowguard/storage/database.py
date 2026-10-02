"""SQLite persistence for workflow runs.

Stores run summaries, per-step results (including captured stdout/stderr
logs), and a per-run event log. The database is plain SQLite — a single
file, no server, no external service.

File databases open a fresh connection per operation (WAL mode), which
makes the store safe to use from the engine's worker threads and the REST
API concurrently. In-memory databases share one lock-protected connection.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import TYPE_CHECKING, Any

from flowguard.core.models import PipelineResult, Status, StepResult

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["Database", "default_db_path"]

_SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    workflow_name  TEXT NOT NULL,
    config_path    TEXT,
    status         TEXT NOT NULL,
    started_at     REAL NOT NULL,
    finished_at    REAL,
    duration_ms    REAL,
    total_steps    INTEGER NOT NULL DEFAULT 0,
    schema_version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS step_results (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    step_name   TEXT NOT NULL,
    status      TEXT NOT NULL,
    exit_code   INTEGER,
    stdout      TEXT NOT NULL DEFAULT '',
    stderr      TEXT NOT NULL DEFAULT '',
    error       TEXT,
    duration_ms REAL NOT NULL DEFAULT 0,
    attempts    INTEGER NOT NULL DEFAULT 0,
    started_at  REAL,
    finished_at REAL,
    artifacts   TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
    timestamp REAL NOT NULL,
    level     TEXT NOT NULL DEFAULT 'info',
    message   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE INDEX IF NOT EXISTS idx_steps_run ON step_results(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
"""


def default_db_path() -> Path:
    """Default database location: ``.flowguard/flowguard.db`` under the CWD."""
    return Path.cwd() / ".flowguard" / "flowguard.db"


def _open_connection(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    with suppress(sqlite3.DatabaseError):  # memory DBs reject WAL
        conn.execute("PRAGMA journal_mode = WAL")
    return conn


class Database:
    """Run history store backed by SQLite.

    ``path`` may be a file system location (created on demand) or the
    literal ``":memory:"`` for an in-memory store (useful for tests and the
    API server).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self._is_memory = str(self.path) == ":memory:"
        self._lock = threading.Lock()
        self._memory_conn: sqlite3.Connection | None = None
        if self._is_memory:
            self._memory_conn = _open_connection(":memory:")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        if self._is_memory:
            with self._lock:
                assert self._memory_conn is not None
                yield self._memory_conn
        else:
            conn = _open_connection(str(self.path))
            try:
                yield conn
            finally:
                conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(_SCHEMA)
            conn.commit()

    def close(self) -> None:
        """Release the shared connection (in-memory mode only)."""
        if self._memory_conn is not None:
            self._memory_conn.close()
            self._memory_conn = None

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- runs ---------------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        workflow_name: str,
        *,
        config_path: str | None = None,
        total_steps: int = 0,
        started_at: float,
        status: Status = Status.RUNNING,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO runs (run_id, workflow_name, config_path, status,"
                " started_at, total_steps, schema_version)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    workflow_name,
                    config_path,
                    status.value,
                    started_at,
                    total_steps,
                    _SCHEMA_VERSION,
                ),
            )
            conn.commit()

    def update_run(
        self,
        run_id: str,
        *,
        status: Status,
        finished_at: float,
        duration_ms: float,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE runs SET status = ?, finished_at = ?, duration_ms = ? WHERE run_id = ?",
                (status.value, finished_at, duration_ms, run_id),
            )
            conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def list_runs(
        self,
        *,
        limit: int = 20,
        workflow_name: str | None = None,
        status: Status | None = None,
    ) -> list[dict[str, Any]]:
        """Most recent runs first, optionally filtered."""
        query = "SELECT * FROM runs"
        conditions: list[str] = []
        params: list[Any] = []
        if workflow_name is not None:
            conditions.append("workflow_name = ?")
            params.append(workflow_name)
        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY started_at DESC, rowid DESC LIMIT ?"
        params.append(limit)
        with self._connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

    def delete_run(self, run_id: str) -> bool:
        with self._connection() as conn:
            cursor = conn.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))
            conn.commit()
        return cursor.rowcount > 0

    def count_runs(self) -> int:
        with self._connection() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()
        return int(row["n"]) if row is not None else 0

    # -- step results ---------------------------------------------------------

    def add_step_result(self, run_id: str, result: StepResult) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO step_results (run_id, step_name, status, exit_code,"
                " stdout, stderr, error, duration_ms, attempts, started_at,"
                " finished_at, artifacts)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    result.step_name,
                    result.status.value,
                    result.exit_code,
                    result.stdout,
                    result.stderr,
                    result.error,
                    result.duration_ms,
                    result.attempts,
                    result.started_at or None,
                    result.finished_at or None,
                    json.dumps(result.artifacts),
                ),
            )
            conn.commit()

    def get_step_results(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM step_results WHERE run_id = ? ORDER BY id",
                (run_id,),
            ).fetchall()
        results = []
        for row in rows:
            item = dict(row)
            item["artifacts"] = json.loads(item.get("artifacts") or "[]")
            results.append(item)
        return results

    def count_step_results(self, run_id: str) -> int:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM step_results WHERE run_id = ?", (run_id,)
            ).fetchone()
        return int(row["n"]) if row is not None else 0

    # -- event log --------------------------------------------------------------

    def add_event(
        self,
        run_id: str,
        message: str,
        *,
        level: str = "info",
        timestamp: float | None = None,
    ) -> None:
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO events (run_id, timestamp, level, message) VALUES (?, ?, ?, ?)",
                (run_id, timestamp if timestamp is not None else time.time(), level, message),
            )
            conn.commit()

    def get_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT * FROM events WHERE run_id = ? ORDER BY id", (run_id,)
            ).fetchall()
        return [dict(row) for row in rows]

    # -- combined views -----------------------------------------------------------

    def record_pipeline(self, result: PipelineResult, *, config_path: str | None = None) -> None:
        """Persist a finished :class:`PipelineResult` in one call."""
        self.create_run(
            result.run_id,
            result.workflow_name,
            config_path=config_path,
            total_steps=len(result.step_results),
            started_at=result.started_at,
            status=Status.RUNNING,
        )
        for step_result in result.step_results:
            self.add_step_result(result.run_id, step_result)
        self.update_run(
            result.run_id,
            status=result.status,
            finished_at=result.finished_at,
            duration_ms=result.duration_ms,
        )
