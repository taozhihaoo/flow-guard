"""Regression tests for defects found in the 0.2.0 audit.

Each test maps to a concrete bug that was reproducible against 0.1.0:

* field-level validation errors were swallowed into "internal error";
* artifact globs could escape the run folder or crash the engine;
* step logs were only written for steps declaring artifacts;
* ``--no-artifacts`` did not actually disable collection;
* the API resolved ``working_dir`` against the server CWD, not the file;
* timeouts killed the shell but left its children running;
* retries discarded earlier attempts' output;
* a crashed run stayed ``running`` forever in SQLite;
* ``flowguard init --path nested/...`` raised a raw traceback.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from flowguard.api.app import create_app
from flowguard.cli.commands import app
from flowguard.config.parser import parse_yaml_text
from flowguard.core.engine import ExecutionEngine
from flowguard.core.exceptions import ConfigError
from flowguard.core.models import RetryPolicy, Status, Step, StepResult, Workflow
from flowguard.runner.executor import CommandExecutor
from flowguard.runner.retry import RetryExecutor
from flowguard.storage.artifacts import ArtifactCollector
from flowguard.storage.database import Database

runner = CliRunner()


# -- configuration validation -------------------------------------------------


class TestValidationErrorReporting:
    @pytest.mark.parametrize(
        ("yaml_text", "expected"),
        [
            (
                "workflow:\n  name: ok\n  steps:\n    - name: 'bad name!'\n"
                "      command: [echo, hi]\n",
                "invalid step name",
            ),
            (
                "workflow:\n  name: ok\n  steps:\n    - name: a\n      command: '   '\n",
                "command must not be empty",
            ),
            (
                "workflow:\n  name: ok\n  steps:\n    - name: a\n"
                "      command: [echo, hi]\n      environment:\n        FOO: 1\n",
                "must map to a string",
            ),
            (
                "workflow:\n  name: ok\n  environment:\n    FOO: 1\n"
                "  steps:\n    - name: a\n      command: [echo, hi]\n",
                "must map to a string",
            ),
        ],
    )
    def test_field_errors_are_human_readable(self, yaml_text: str, expected: str):
        with pytest.raises(ConfigError) as caught:
            parse_yaml_text(yaml_text)
        message = str(caught.value)
        assert expected in message
        assert "internal error" not in message

    def test_workflow_environment_validator_matches_step_validator(self):
        with pytest.raises(ConfigError, match="environment must be a mapping"):
            parse_yaml_text(
                "workflow:\n  name: ok\n  environment: [A, B]\n"
                "  steps:\n    - name: a\n      command: [echo, hi]\n"
            )


# -- artifact sandbox ----------------------------------------------------------


class TestArtifactSandbox:
    def test_parent_traversal_pattern_rejected(self, tmp_path: Path):
        (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
        work = tmp_path / "project"
        work.mkdir()
        collector = ArtifactCollector(work / ".flowguard" / "artifacts")
        with pytest.raises(ValueError, match="escape the working directory"):
            collector.collect("r1", "build", ["../outside.txt"], cwd=work)

    def test_absolute_pattern_rejected_with_readable_error(self, tmp_path: Path):
        target = tmp_path / "absolute.txt"
        target.write_text("x", encoding="utf-8")
        collector = ArtifactCollector(tmp_path / "artifacts")
        with pytest.raises(ValueError, match="must be relative"):
            collector.collect("r1", "build", [str(target)], cwd=tmp_path)

    def test_working_directory_itself_rejected(self, tmp_path: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        with pytest.raises(ValueError, match="not the working directory itself"):
            collector.collect("r1", "build", ["."], cwd=tmp_path)

    def test_normal_relative_globs_still_work(self, tmp_path: Path):
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "app.bin").write_text("binary", encoding="utf-8")
        collector = ArtifactCollector(tmp_path / "artifacts")
        assert collector.collect("r1", "build", ["dist/*"], cwd=tmp_path) == ["dist/app.bin"]


class TestEngineArtifactHandling:
    @staticmethod
    def _workflow(artifacts: list[str]) -> Workflow:
        return Workflow.model_validate(
            {
                "name": "artifacts-demo",
                "steps": [
                    {
                        "name": "build",
                        "command": [sys.executable, "-c", "print('logged output')"],
                        "artifacts": artifacts,
                    }
                ],
            }
        )

    async def test_bad_artifact_pattern_fails_step_not_pipeline(self, tmp_path: Path):
        absolute = (tmp_path / "somewhere.txt").as_posix()
        workflow = self._workflow([absolute])
        result = await ExecutionEngine(artifact_root=tmp_path / "out").run(
            workflow, config_dir=tmp_path
        )
        assert result.status is Status.FAILED
        assert result.step("build") is not None
        assert result.step("build").status is Status.FAILED  # type: ignore[union-attr]
        assert "artifact collection failed" in (result.step("build").error or "")  # type: ignore[union-attr]

    async def test_logs_written_even_without_declared_artifacts(self, tmp_path: Path):
        result = await ExecutionEngine().run(self._workflow([]), config_dir=tmp_path)
        assert result.succeeded
        log = tmp_path / ".flowguard" / "artifacts" / result.run_id / "logs" / "build.log"
        assert log.exists()
        assert "logged output" in log.read_text(encoding="utf-8")

    async def test_collect_artifacts_false_disables_everything(self, tmp_path: Path):
        result = await ExecutionEngine(collect_artifacts=False).run(
            self._workflow([]), config_dir=tmp_path
        )
        assert result.succeeded
        assert not (tmp_path / ".flowguard" / "artifacts").exists()


# -- API working directory -----------------------------------------------------


class TestApiConfigDir:
    def test_relative_working_dir_resolves_against_workflow_file(self, tmp_path: Path):
        project = tmp_path / "project"
        (project / "src").mkdir(parents=True)
        (project / "src" / "marker.txt").write_text("ok", encoding="utf-8")
        config = project / "pipeline.yml"
        config.write_text(
            "workflow:\n"
            "  name: relative-cwd\n"
            "  steps:\n"
            "    - name: check\n"
            f'      command: [{sys.executable}, -c, "import pathlib,sys; '
            "sys.exit(0 if pathlib.Path('marker.txt').exists() else 1)\"]\n"
            "      working_dir: src\n",
            encoding="utf-8",
        )
        client = TestClient(create_app(db_path=":memory:"))
        body = client.post("/execute", json={"path": str(config)}).json()
        assert body["status"] == "succeeded"
        assert body["steps"][0]["exit_code"] == 0


# -- timeout process tree ------------------------------------------------------


class TestTimeoutKillsProcessTree:
    def test_shell_grandchild_is_terminated(self, tmp_path: Path):
        marker = tmp_path / "grandchild_finished.txt"
        code = (
            "import time, pathlib; time.sleep(1.2); "
            f"pathlib.Path(r'{marker}').write_text('still alive')"
        )
        step = Step(name="shell-child", command=f'{sys.executable} -c "{code}"', timeout=0.3)
        started = time.monotonic()
        result = CommandExecutor().execute(step, cwd=tmp_path)
        elapsed = time.monotonic() - started
        assert result.status is Status.TIMEOUT
        assert elapsed < 3.0  # the executor must not wait for the grandchild
        time.sleep(1.4)
        assert not marker.exists()


# -- retry transcript ----------------------------------------------------------


class TestRetryTranscript:
    def test_earlier_attempt_output_is_preserved(self):
        outputs = iter(
            [
                StepResult(step_name="a", status=Status.FAILED, stdout="first", started_at=10.0),
                StepResult(
                    step_name="a", status=Status.SUCCEEDED, stdout="second", started_at=12.0
                ),
            ]
        )
        result = RetryExecutor(sleep=lambda _: None).run(
            lambda: next(outputs), RetryPolicy(attempts=2, delay=0)
        )
        assert result.attempts == 2
        assert "first" in result.stdout
        assert "second" in result.stdout
        assert result.started_at == 10.0

    def test_empty_transcripts_stay_empty(self):
        outputs = iter(
            [
                StepResult(step_name="a", status=Status.FAILED),
                StepResult(step_name="a", status=Status.SUCCEEDED),
            ]
        )
        result = RetryExecutor(sleep=lambda _: None).run(
            lambda: next(outputs), RetryPolicy(attempts=2, delay=0)
        )
        assert result.stdout == ""
        assert result.stderr == ""


# -- crash safety --------------------------------------------------------------


class TestRunCrashSafety:
    async def test_failed_run_is_not_left_in_running_state(self, tmp_path: Path, monkeypatch):
        db = Database(":memory:")
        engine = ExecutionEngine(storage=db)
        workflow = Workflow.model_validate(
            {
                "name": "crash-demo",
                "steps": [{"name": "a", "command": [sys.executable, "-c", "pass"]}],
            }
        )

        async def boom(*_args: object, **_kwargs: object):
            raise RuntimeError("boom")

        monkeypatch.setattr(engine, "_schedule", boom)
        with pytest.raises(RuntimeError, match="boom"):
            await engine.run(workflow, config_dir=tmp_path, run_id="crashrun")
        record = db.get_run("crashrun")
        assert record is not None
        assert record["status"] == Status.FAILED.value


# -- SQLite concurrency --------------------------------------------------------


class TestConcurrentWrites:
    def test_many_threads_write_without_transient_errors(self, tmp_path: Path):
        import threading

        db = Database(tmp_path / "concurrent.db")
        db.create_run("shared", "wf", started_at=1.0)
        errors: list[Exception] = []

        def write(index: int) -> None:
            try:
                db.add_step_result(
                    "shared",
                    StepResult(
                        step_name=f"step-{index}",
                        status=Status.SUCCEEDED,
                        attempts=1,
                    ),
                )
            except Exception as exc:  # record any transient failure
                errors.append(exc)

        threads = [threading.Thread(target=write, args=(i,)) for i in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        assert db.count_step_results("shared") == 16


# -- CLI init ------------------------------------------------------------------


class TestInitNestedPath:
    def test_creates_missing_parent_directories(self, tmp_path: Path):
        target = tmp_path / "nested" / "config" / ".flowguard.yml"
        result = runner.invoke(app, ["init", "--path", str(target)])
        assert result.exit_code == 0
        assert target.exists()
        assert "workflow:" in target.read_text(encoding="utf-8")


# -- CLI language --------------------------------------------------------------


class TestCliLanguage:
    def test_chinese_flag_renders_translated_labels(self, tmp_path: Path):
        config = tmp_path / ".flowguard.yml"
        config.write_text(
            "workflow:\n  name: lang-demo\n  steps:\n"
            f"    - name: ok\n      command: [{sys.executable}, -c, pass]\n",
            encoding="utf-8",
        )
        result = runner.invoke(
            app,
            ["--lang", "zh", "run", str(config), "--db", str(tmp_path / "h.db")],
        )
        assert result.exit_code == 0, result.output
        assert "流水线执行成功" in result.output

    def test_default_language_stays_english(self, tmp_path: Path, monkeypatch):
        monkeypatch.delenv("FLOWGUARD_LANG", raising=False)
        result = runner.invoke(app, ["validate", str(tmp_path / "ghost.yml")])
        assert "Configuration error" in result.output + (result.stderr or "")
