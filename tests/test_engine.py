"""Tests for the execution engine: scheduling, parallelism, storage wiring (Phase 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import python_step
from flowguard import ExecutionEngine
from flowguard.core.exceptions import CycleError
from flowguard.core.models import Status, Step, Workflow
from flowguard.plugins.base import PipelineContext, Plugin
from flowguard.plugins.manager import PluginManager
from flowguard.storage.database import Database

# A rendezvous: both steps only succeed if they run *concurrently*.
RENDEZVOUS_A = """\
import pathlib, time
one, two = pathlib.Path('one'), pathlib.Path('two')
one.write_text('x')
deadline = time.time() + 10
while not two.exists() and time.time() < deadline:
    time.sleep(0.02)
raise SystemExit(0 if two.exists() else 2)
"""
RENDEZVOUS_B = """\
import pathlib, time
one, two = pathlib.Path('one'), pathlib.Path('two')
deadline = time.time() + 10
while not one.exists() and time.time() < deadline:
    time.sleep(0.02)
two.write_text('y')
raise SystemExit(0 if one.exists() else 2)
"""


async def run_engine(workflow: Workflow, tmp_path: Path, **kwargs: object):
    engine = ExecutionEngine(**kwargs)  # type: ignore[arg-type]
    return await engine.run(workflow, config_dir=tmp_path)


class TestBasicExecution:
    async def test_linear_pipeline_succeeds(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("a", "print('A')"),
            python_step("b", "print('B')", depends_on=["a"]),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.succeeded
        assert [r.status for r in result.step_results] == [Status.SUCCEEDED] * 2
        assert result.step("a").stdout.strip() == "A"

    async def test_dependency_order_respected(self, make_workflow, tmp_path):
        append = "import pathlib; pathlib.Path('order.txt').open('a').write('%s\\n')"
        workflow = make_workflow(
            python_step("first", append % "a"),
            python_step("second", append % "b", depends_on=["first"]),
            python_step("third", append % "c", depends_on=["second"]),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.succeeded
        assert (tmp_path / "order.txt").read_text().split() == ["a", "b", "c"]

    async def test_step_results_in_declaration_order(self, make_workflow, tmp_path):
        # Dependencies declared out of order must not change reporting order.
        workflow = make_workflow(
            python_step("z", "pass"),
            python_step("a", "pass", depends_on=["z"]),
            python_step("m", "pass"),
        )
        result = await run_engine(workflow, tmp_path)
        assert [r.step_name for r in result.step_results] == ["z", "a", "m"]

    async def test_run_ids_are_unique(self, make_workflow, tmp_path):
        workflow = make_workflow(python_step("a", "pass"))
        first = await run_engine(workflow, tmp_path)
        second = await run_engine(workflow, tmp_path)
        assert first.run_id != second.run_id

    async def test_duration_recorded(self, make_workflow, tmp_path):
        workflow = make_workflow(python_step("a", "import time; time.sleep(0.2)"))
        result = await run_engine(workflow, tmp_path)
        assert result.duration_ms >= 180


class TestFailureSemantics:
    async def test_failed_step_fails_pipeline(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("boom", "raise SystemExit(1)"),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.status is Status.FAILED
        assert result.step("boom").exit_code == 1

    async def test_dependents_skipped_on_failure(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("boom", "raise SystemExit(2)"),
            python_step("child", "pass", depends_on=["boom"]),
        )
        result = await run_engine(workflow, tmp_path)
        skipped = result.step("child")
        assert skipped.status is Status.SKIPPED
        assert "boom" in (skipped.error or "")

    async def test_skip_is_transitive(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("boom", "raise SystemExit(1)"),
            python_step("mid", "pass", depends_on=["boom"]),
            python_step("leaf", "pass", depends_on=["mid"]),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("mid").status is Status.SKIPPED
        assert result.step("leaf").status is Status.SKIPPED

    async def test_independent_branch_still_runs(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("boom", "raise SystemExit(1)"),
            python_step("orphan", "print('alive')"),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.status is Status.FAILED
        assert result.step("orphan").succeeded
        assert "alive" in result.step("orphan").stdout

    async def test_sibling_subtree_runs_but_downstream_skipped(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("boom", "raise SystemExit(1)"),
            python_step("healthy", "pass"),
            python_step("after-healthy", "pass", depends_on=["healthy"]),
            python_step("after-boom", "pass", depends_on=["boom"]),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("healthy").succeeded
        assert result.step("after-healthy").succeeded
        assert result.step("after-boom").status is Status.SKIPPED

    async def test_timeout_fails_step_and_skips_dependents(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("slow", "import time; time.sleep(30)", timeout=0.5),
            python_step("next", "pass", depends_on=["slow"]),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("slow").status is Status.TIMEOUT
        assert result.step("next").status is Status.SKIPPED
        assert result.status is Status.FAILED


class TestParallelism:
    async def test_independent_steps_run_concurrently(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("a", RENDEZVOUS_A),
            python_step("b", RENDEZVOUS_B),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.succeeded, [r.error for r in result.step_results]

    async def test_parallel_wall_clock_beats_serial(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("s1", "import time; time.sleep(0.5)"),
            python_step("s2", "import time; time.sleep(0.5)"),
            python_step("s3", "import time; time.sleep(0.5)"),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.succeeded
        # Serial execution would need >= 1.5s; parallel finishes near 0.5s.
        assert result.duration_ms < 1400

    async def test_max_workers_one_serialises(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("s1", "import time; time.sleep(0.3)"),
            python_step("s2", "import time; time.sleep(0.3)"),
        )
        result = await run_engine(workflow, tmp_path, max_workers=1)
        assert result.succeeded
        assert result.duration_ms >= 550

    async def test_dependent_never_starts_before_dependency(self, make_workflow, tmp_path):
        # "second" writes a marker only if "first" already wrote it.
        workflow = make_workflow(
            python_step("first", "import pathlib; pathlib.Path('flag').write_text('1')"),
            python_step(
                "second",
                "import pathlib, sys; sys.exit(0 if pathlib.Path('flag').exists() else 1)",
                depends_on=["first"],
            ),
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("second").succeeded


class TestEnvironmentLayers:
    async def test_env_override_reaches_child(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step(
                "a",
                "import os; print(os.environ['FLOWGUARD_X'])",
            )
        )
        engine = ExecutionEngine()
        result = await engine.run(
            workflow, config_dir=tmp_path, env_overrides={"FLOWGUARD_X": "override"}
        )
        assert result.step("a").stdout.strip() == "override"

    async def test_workflow_env_reaches_child(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("a", "import os; print(os.environ['FLOWGUARD_X'])"),
            environment={"FLOWGUARD_X": "workflow"},
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("a").stdout.strip() == "workflow"

    async def test_step_env_beats_workflow_env(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step(
                "a",
                "import os; print(os.environ['FLOWGUARD_X'])",
                environment={"FLOWGUARD_X": "step"},
            ),
            environment={"FLOWGUARD_X": "workflow"},
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("a").stdout.strip() == "step"

    async def test_parent_env_inherited(self, make_workflow, tmp_path, monkeypatch):
        monkeypatch.setenv("FLOWGUARD_PARENT", "yes")
        workflow = make_workflow(
            python_step("a", "import os; print(os.environ['FLOWGUARD_PARENT'])")
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("a").stdout.strip() == "yes"

    async def test_cli_override_beats_step_env(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step(
                "a",
                "import os; print(os.environ['FLOWGUARD_X'])",
                environment={"FLOWGUARD_X": "step"},
            ),
        )
        engine = ExecutionEngine()
        result = await engine.run(
            workflow, config_dir=tmp_path, env_overrides={"FLOWGUARD_X": "cli"}
        )
        assert result.step("a").stdout.strip() == "cli"


class TestRetryIntegration:
    async def test_engine_retries_failed_step(self, make_workflow, tmp_path):
        # Fails on the first attempt, creates a marker, succeeds on the second.
        code = (
            "import pathlib, sys\n"
            "marker = pathlib.Path('marker')\n"
            "if marker.exists():\n"
            "    sys.exit(0)\n"
            "marker.write_text('x')\n"
            "sys.exit(1)\n"
        )
        workflow = make_workflow(python_step("flaky", code, retry={"attempts": 3, "delay": 0}))
        result = await run_engine(workflow, tmp_path)
        assert result.succeeded
        assert result.step("flaky").attempts == 2

    async def test_retries_exhausted_records_last_error(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step(
                "always-fails",
                "raise SystemExit(7)",
                retry={"attempts": 2, "delay": 0},
            )
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("always-fails").attempts == 2
        assert result.step("always-fails").exit_code == 7


class TestEngineWithStorage:
    async def test_run_and_steps_persisted(self, make_workflow, tmp_path):
        db = Database(":memory:")
        workflow = make_workflow(
            python_step("a", "print('out')"),
            python_step("b", "raise SystemExit(1)", depends_on=["a"]),
            python_step("c", "pass", depends_on=["b"]),
        )
        result = await ExecutionEngine(storage=db).run(workflow, config_dir=tmp_path)
        record = db.get_run(result.run_id)
        assert record is not None
        assert record["status"] == "failed"
        assert record["workflow_name"] == workflow.name
        assert record["total_steps"] == 3

        steps = db.get_step_results(result.run_id)
        assert [s["step_name"] for s in steps] == ["a", "b", "c"]
        assert steps[0]["stdout"].strip() == "out"
        assert steps[1]["exit_code"] == 1
        assert steps[2]["status"] == "skipped"

    async def test_events_recorded(self, make_workflow, tmp_path):
        db = Database(":memory:")
        workflow = make_workflow(python_step("a", "pass"))
        result = await ExecutionEngine(storage=db).run(workflow, config_dir=tmp_path)
        messages = " | ".join(e["message"] for e in db.get_events(result.run_id))
        assert "run started" in messages
        assert "step 'a' finished" in messages
        assert "run finished" in messages

    async def test_plugin_errors_landed_in_events(self, make_workflow, tmp_path):
        class Exploding(Plugin):
            name = "exploding"

            def before_pipeline(self, context: PipelineContext) -> None:
                raise RuntimeError("boom")

        db = Database(":memory:")
        plugins = PluginManager()
        plugins.register(Exploding())
        workflow = make_workflow(python_step("a", "pass"))
        result = await ExecutionEngine(storage=db, plugins=plugins).run(
            workflow, config_dir=tmp_path
        )
        # The run still completes.
        assert result.succeeded
        messages = " | ".join(e["message"] for e in db.get_events(result.run_id))
        assert "exploding" in messages


class TestEngineGuardRails:
    async def test_cyclic_workflow_rejected_before_execution(self, tmp_path):
        # A programmatically built model bypasses parser checks (which reject
        # cycles eagerly); the engine must still refuse to execute it.
        workflow = Workflow.model_validate(
            {
                "name": "cyclic",
                "steps": [
                    {"name": "a", "command": ["echo", "a"], "depends_on": ["b"]},
                    {"name": "b", "command": ["echo", "b"], "depends_on": ["a"]},
                ],
            }
        )
        with pytest.raises(CycleError):
            await run_engine(workflow, tmp_path)

    async def test_working_dir_resolution(self, make_workflow, tmp_path):
        (tmp_path / "sub").mkdir()
        workflow = make_workflow(
            python_step("a", "import os; print(os.getcwd())", working_dir="sub")
        )
        result = await run_engine(workflow, tmp_path)
        assert Path(result.step("a").stdout.strip()).resolve() == (tmp_path / "sub").resolve()

    async def test_working_dir_relative_to_config(self, make_workflow, tmp_path):
        (tmp_path / "nested").mkdir()
        workflow = make_workflow(
            python_step(
                "a",
                "import pathlib; pathlib.Path('made.txt').write_text('x')",
                working_dir="nested",
            )
        )
        result = await run_engine(workflow, tmp_path)
        assert result.succeeded
        assert (tmp_path / "nested" / "made.txt").exists()


class TestArtifactIntegration:
    async def test_declared_artifacts_collected(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step(
                "produce",
                "import pathlib; pathlib.Path('out').mkdir(); "
                "pathlib.Path('out/result.txt').write_text('data')",
                artifacts=["out/*"],
            )
        )
        result = await run_engine(workflow, tmp_path)
        assert result.step("produce").artifacts == ["out/result.txt"]
        saved = (
            tmp_path / ".flowguard" / "artifacts" / result.run_id / "produce" / "out" / "result.txt"
        )
        assert saved.read_text(encoding="utf-8") == "data"

    async def test_step_logs_saved_with_artifacts(self, make_workflow, tmp_path):
        workflow = make_workflow(
            python_step("talkative", "print('logged output')", artifacts=["*.log"])
        )
        result = await run_engine(workflow, tmp_path)
        log = tmp_path / ".flowguard" / "artifacts" / result.run_id / "logs" / "talkative.log"
        assert "logged output" in log.read_text(encoding="utf-8")


class TestPluginHookOrder:
    async def test_hooks_fire_in_documented_order(self, make_workflow, tmp_path):
        events: list[str] = []

        class Recorder(Plugin):
            name = "recorder"

            def before_pipeline(self, context: PipelineContext) -> None:
                events.append("before_pipeline")

            def before_step(self, context: PipelineContext, step: Step) -> None:
                events.append(f"before_step:{step.name}")

            def after_step(self, context: PipelineContext, step: Step, result) -> None:
                events.append(f"after_step:{step.name}")

            def after_pipeline(self, context: PipelineContext, result) -> None:
                events.append("after_pipeline")

        plugins = PluginManager()
        plugins.register(Recorder())
        workflow = make_workflow(
            python_step("a", "pass"),
            python_step("b", "pass", depends_on=["a"]),
        )
        await ExecutionEngine(plugins=plugins).run(workflow, config_dir=tmp_path)
        assert events == [
            "before_pipeline",
            "before_step:a",
            "after_step:a",
            "before_step:b",
            "after_step:b",
            "after_pipeline",
        ]
