"""Tests for SQLite run-history storage (Phase 7)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from flowguard.core.models import Status, StepResult
from flowguard.storage.database import Database


def step_result(
    name: str,
    status: Status = Status.SUCCEEDED,
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int | None = 0,
    artifacts: list[str] | None = None,
    attempts: int = 1,
    duration_ms: float = 10.0,
) -> StepResult:
    return StepResult(
        step_name=name,
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        error=None if status is Status.SUCCEEDED else "it broke",
        duration_ms=duration_ms,
        attempts=attempts,
        started_at=1.0,
        finished_at=2.0,
        artifacts=artifacts or [],
    )


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    return Database(tmp_path / "history" / "flowguard.db")


@pytest.fixture()
def stored_run(db: Database) -> str:
    db.create_run("run-1", "demo", config_path="cfg.yml", total_steps=2, started_at=100.0)
    return "run-1"


class TestRuns:
    def test_create_and_get_run(self, db: Database, stored_run: str):
        record = db.get_run(stored_run)
        assert record is not None
        assert record["workflow_name"] == "demo"
        assert record["config_path"] == "cfg.yml"
        assert record["status"] == "running"
        assert record["total_steps"] == 2
        assert record["started_at"] == 100.0

    def test_get_missing_run_returns_none(self, db: Database):
        assert db.get_run("nope") is None

    def test_update_run_finalises(self, db: Database, stored_run: str):
        db.update_run(stored_run, status=Status.SUCCEEDED, finished_at=150.0, duration_ms=5000.0)
        record = db.get_run(stored_run)
        assert record["status"] == "succeeded"
        assert record["finished_at"] == 150.0
        assert record["duration_ms"] == 5000.0

    def test_db_created_in_nested_directory(self, db: Database, tmp_path: Path):
        assert (tmp_path / "history" / "flowguard.db").exists()

    def test_persistence_across_instances(self, tmp_path: Path):
        path = tmp_path / "h.db"
        first = Database(path)
        first.create_run("r", "wf", started_at=1.0)
        first.close()
        second = Database(path)
        assert second.get_run("r")["workflow_name"] == "wf"

    def test_in_memory_database(self):
        memory = Database(":memory:")
        memory.create_run("m1", "wf", started_at=1.0)
        assert memory.get_run("m1") is not None
        assert memory.get_run("m2") is None

    def test_count_runs(self, db: Database):
        assert db.count_runs() == 0
        db.create_run("a", "wf", started_at=1.0)
        db.create_run("b", "wf", started_at=2.0)
        assert db.count_runs() == 2

    def test_delete_run_cascades_steps(self, db: Database, stored_run: str):
        db.add_step_result(stored_run, step_result("a"))
        assert db.delete_run(stored_run) is True
        assert db.get_run(stored_run) is None
        assert db.get_step_results(stored_run) == []
        assert db.delete_run(stored_run) is False


class TestListRuns:
    def test_most_recent_first(self, db: Database):
        db.create_run("old", "wf", started_at=1.0)
        db.create_run("new", "wf", started_at=2.0)
        runs = db.list_runs()
        assert [r["run_id"] for r in runs] == ["new", "old"]

    def test_limit(self, db: Database):
        for i in range(5):
            db.create_run(f"r{i}", "wf", started_at=float(i))
        assert len(db.list_runs(limit=3)) == 3

    def test_filter_by_workflow(self, db: Database):
        db.create_run("a", "alpha", started_at=1.0)
        db.create_run("b", "beta", started_at=2.0)
        runs = db.list_runs(workflow_name="alpha")
        assert [r["run_id"] for r in runs] == ["a"]

    def test_filter_by_status(self, db: Database):
        db.create_run("a", "wf", started_at=1.0)
        db.update_run("a", status=Status.FAILED, finished_at=2.0, duration_ms=1.0)
        db.create_run("b", "wf", started_at=3.0)
        assert [r["run_id"] for r in db.list_runs(status=Status.FAILED)] == ["a"]
        assert [r["run_id"] for r in db.list_runs(status=Status.RUNNING)] == ["b"]


class TestStepResults:
    def test_add_and_get_round_trip(self, db: Database, stored_run: str):
        db.add_step_result(
            stored_run,
            step_result(
                "build",
                stdout="out-text",
                stderr="err-text",
                exit_code=0,
                artifacts=["dist/app.whl"],
                attempts=2,
                duration_ms=1234.5,
            ),
        )
        (row,) = db.get_step_results(stored_run)
        assert row["step_name"] == "build"
        assert row["status"] == "succeeded"
        assert row["stdout"] == "out-text"
        assert row["stderr"] == "err-text"
        assert row["exit_code"] == 0
        assert row["artifacts"] == ["dist/app.whl"]
        assert row["attempts"] == 2
        assert row["duration_ms"] == pytest.approx(1234.5)
        assert row["error"] is None

    def test_order_preserved(self, db: Database, stored_run: str):
        for name in ("a", "b", "c"):
            db.add_step_result(stored_run, step_result(name))
        assert [r["step_name"] for r in db.get_step_results(stored_run)] == ["a", "b", "c"]

    def test_failed_result_fields(self, db: Database, stored_run: str):
        db.add_step_result(stored_run, step_result("f", Status.FAILED, exit_code=9))
        (row,) = db.get_step_results(stored_run)
        assert row["status"] == "failed"
        assert row["error"] == "it broke"
        assert row["exit_code"] == 9

    def test_count_step_results(self, db: Database, stored_run: str):
        db.add_step_result(stored_run, step_result("a"))
        db.add_step_result(stored_run, step_result("b"))
        assert db.count_step_results(stored_run) == 2
        assert db.count_step_results("other") == 0

    def test_foreign_key_enforced(self, db: Database):
        with pytest.raises(sqlite3.IntegrityError):
            db.add_step_result("ghost-run", step_result("a"))


class TestEvents:
    def test_add_and_get_in_order(self, db: Database, stored_run: str):
        db.add_event(stored_run, "first", timestamp=1.0)
        db.add_event(stored_run, "second", timestamp=2.0, level="warning")
        events = db.get_events(stored_run)
        assert [(e["message"], e["level"]) for e in events] == [
            ("first", "info"),
            ("second", "warning"),
        ]
        assert events[0]["timestamp"] == 1.0

    def test_default_timestamp_is_now(self, db: Database, stored_run: str):
        import time

        before = time.time()
        db.add_event(stored_run, "tick")
        after = time.time()
        timestamp = db.get_events(stored_run)[0]["timestamp"]
        assert before <= timestamp <= after


class TestRecordPipeline:
    def test_full_pipeline_recorded(self, db: Database):
        from flowguard.core.models import PipelineResult

        result = PipelineResult(
            run_id="pipe-1",
            workflow_name="wf",
            status=Status.SUCCEEDED,
            step_results=[step_result("a"), step_result("b")],
            started_at=10.0,
            finished_at=20.0,
            duration_ms=10_000.0,
        )
        db.record_pipeline(result, config_path="x.yml")
        record = db.get_run("pipe-1")
        assert record["status"] == "succeeded"
        assert record["duration_ms"] == 10_000.0
        assert record["total_steps"] == 2
        assert db.count_step_results("pipe-1") == 2


class TestThreading:
    def test_concurrent_writes_from_threads(self, tmp_path: Path):
        import threading

        db = Database(tmp_path / "t.db")
        db.create_run("shared", "wf", started_at=1.0)

        def write(index: int) -> None:
            db.add_step_result("shared", step_result(f"step-{index}"))

        threads = [threading.Thread(target=write, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert db.count_step_results("shared") == 8
