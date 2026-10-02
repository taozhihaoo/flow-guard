"""Tests for the Typer/Rich CLI (Phase 9)."""

from __future__ import annotations

import sys
from pathlib import Path

from typer.testing import CliRunner

from flowguard.cli.commands import app

runner = CliRunner()

OK_CONFIG = """\
workflow:
  name: cli-demo
  steps:
    - name: hello
      command: [{python}, -c, "print('hello from step')"]
      retry:
        attempts: 2
        delay: 0
"""

FAIL_CONFIG = """\
workflow:
  name: cli-fail
  steps:
    - name: boom
      command: [{python}, -c, "import sys; print('err', file=sys.stderr); raise SystemExit(3)"]

    - name: after
      command: [{python}, -c, "pass"]
      depends_on: [boom]
"""

SLOW_CONFIG = """\
workflow:
  name: cli-slow
  steps:
    - name: sleepy
      command: [{python}, -c, "import time; time.sleep(30)"]
      timeout: 0.5
"""


def make_config(tmp_path: Path, template: str, name: str = ".flowguard.yml") -> Path:
    path = tmp_path / name
    path.write_text(template.format(python=sys.executable), encoding="utf-8")
    return path


def output_of(result) -> str:
    """Combined output across click versions (stdout + stderr)."""
    stdout = result.output
    try:
        stderr = result.stderr or ""
    except Exception:
        stderr = ""
    return stdout + stderr


class TestInit:
    def test_init_creates_default_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0
        assert (tmp_path / ".flowguard.yml").exists()
        assert "Created" in output_of(result)

    def test_init_refuses_overwrite(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".flowguard.yml").write_text("original", encoding="utf-8")
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 1
        assert (tmp_path / ".flowguard.yml").read_text(encoding="utf-8") == "original"

    def test_init_force_overwrites(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".flowguard.yml").write_text("original", encoding="utf-8")
        result = runner.invoke(app, ["init", "--force"])
        assert result.exit_code == 0
        assert "original" not in (tmp_path / ".flowguard.yml").read_text(encoding="utf-8")

    def test_init_custom_path(self, tmp_path: Path):
        target = tmp_path / "custom.yml"
        result = runner.invoke(app, ["init", "--path", str(target)])
        assert result.exit_code == 0
        assert target.exists()


class TestValidate:
    def test_valid_file_passes(self, tmp_path: Path):
        config = make_config(tmp_path, OK_CONFIG)
        result = runner.invoke(app, ["validate", str(config)])
        assert result.exit_code == 0
        assert "valid" in output_of(result).lower()
        assert "cli-demo" in output_of(result)

    def test_invalid_file_fails_with_detail(self, tmp_path: Path):
        config = tmp_path / "bad.yml"
        config.write_text("workflow:\n  name: bad\n  steps:\n    - name: a\n", encoding="utf-8")
        result = runner.invoke(app, ["validate", str(config)])
        assert result.exit_code == 1
        assert "command" in output_of(result)

    def test_missing_file_fails(self, tmp_path: Path):
        result = runner.invoke(app, ["validate", str(tmp_path / "ghost.yml")])
        assert result.exit_code == 1
        assert "not found" in output_of(result)

    def test_yaml_syntax_error_fails(self, tmp_path: Path):
        config = tmp_path / "broken.yml"
        config.write_text("workflow: [", encoding="utf-8")
        result = runner.invoke(app, ["validate", str(config)])
        assert result.exit_code == 1
        assert "YAML" in output_of(result)

    def test_validate_finds_default_file(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        make_config(tmp_path, OK_CONFIG)
        result = runner.invoke(app, ["validate"])
        assert result.exit_code == 0


class TestRun:
    def test_successful_run(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = make_config(tmp_path, OK_CONFIG)
        result = runner.invoke(app, ["run", str(config), "--db", str(tmp_path / "h.db")])
        assert result.exit_code == 0, output_of(result)
        assert "succeeded" in output_of(result)
        assert "hello from step" not in output_of(result)  # stdout captured, not echoed
        assert (tmp_path / "h.db").exists()

    def test_failing_run_exit_code_one(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = make_config(tmp_path, FAIL_CONFIG)
        result = runner.invoke(app, ["run", str(config), "--db", str(tmp_path / "h.db")])
        assert result.exit_code == 1
        combined = output_of(result)
        assert "failed" in combined
        assert "skipped" in combined  # dependent step skipped

    def test_run_timeout_reported(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = make_config(tmp_path, SLOW_CONFIG)
        result = runner.invoke(app, ["run", str(config), "--db", str(tmp_path / "h.db")])
        assert result.exit_code == 1
        assert "timeout" in output_of(result).lower()

    def test_run_step_selection(self, tmp_path: Path, monkeypatch):
        config = make_config(tmp_path, FAIL_CONFIG)
        db = tmp_path / "h.db"
        result = runner.invoke(app, ["run", str(config), "--step", "after", "--db", str(db)])
        # "after" depends on "boom", so it is skipped and the pipeline fails —
        # but crucially only 2 steps were scheduled.
        assert result.exit_code == 1
        assert "boom" in output_of(result)

    def test_run_selected_independent_step_succeeds(self, tmp_path: Path):
        config = tmp_path / "wf.yml"
        config.write_text(
            "workflow:\n"
            "  name: sel\n"
            "  steps:\n"
            "    - name: a\n"
            f'      command: [{sys.executable}, -c, "raise SystemExit(1)"]\n'
            "    - name: b\n"
            f'      command: [{sys.executable}, -c, "pass"]\n',
            encoding="utf-8",
        )
        result = runner.invoke(
            app, ["run", str(config), "--step", "b", "--db", str(tmp_path / "h.db")]
        )
        assert result.exit_code == 0, output_of(result)

    def test_run_unknown_step_errors(self, tmp_path: Path):
        config = make_config(tmp_path, OK_CONFIG)
        result = runner.invoke(
            app, ["run", str(config), "--step", "ghost", "--db", str(tmp_path / "h.db")]
        )
        assert result.exit_code == 2

    def test_run_invalid_env_format(self, tmp_path: Path):
        config = make_config(tmp_path, OK_CONFIG)
        result = runner.invoke(
            app, ["run", str(config), "--env", "NO-SEPARATOR", "--db", str(tmp_path / "h.db")]
        )
        assert result.exit_code == 2
        assert "KEY=VALUE" in output_of(result)

    def test_run_env_reaches_step(self, tmp_path: Path):
        config = tmp_path / "env.yml"
        code = "import os; raise SystemExit(0 if os.environ['FG_CLI']=='yes' else 1)"
        config.write_text(
            "workflow:\n"
            "  name: envrun\n"
            "  steps:\n"
            "    - name: show\n"
            f'      command: [{sys.executable}, -c, "{code}"]\n',
            encoding="utf-8",
        )
        result = runner.invoke(
            app,
            ["run", str(config), "--env", "FG_CLI=yes", "--db", str(tmp_path / "h.db")],
        )
        assert result.exit_code == 0, output_of(result)

    def test_run_no_parallel_flag(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = make_config(tmp_path, OK_CONFIG)
        result = runner.invoke(
            app, ["run", str(config), "--no-parallel", "--db", str(tmp_path / "h.db")]
        )
        assert result.exit_code == 0, output_of(result)

    def test_run_records_history(self, tmp_path: Path):
        config = make_config(tmp_path, OK_CONFIG)
        db = tmp_path / "h.db"
        runner.invoke(app, ["run", str(config), "--db", str(db)])
        result = runner.invoke(app, ["history", "--db", str(db)])
        assert result.exit_code == 0
        assert "cli-demo" in output_of(result)
        assert "succeeded" in output_of(result)

    def test_run_invalid_config_fails(self, tmp_path: Path):
        config = tmp_path / "bad.yml"
        config.write_text("workflow:\n  name: x\n", encoding="utf-8")
        result = runner.invoke(app, ["run", str(config)])
        assert result.exit_code == 1


class TestHistoryAndShow:
    def test_history_empty_database(self, tmp_path: Path):
        result = runner.invoke(app, ["history", "--db", str(tmp_path / "empty.db")])
        assert result.exit_code == 0
        assert "No runs" in output_of(result)

    def test_history_limit_option(self, tmp_path: Path):
        config = make_config(tmp_path, OK_CONFIG)
        db = str(tmp_path / "h.db")
        for _ in range(3):
            runner.invoke(app, ["run", str(config), "--db", db])
        result = runner.invoke(app, ["history", "--db", db, "--limit", "2"])
        assert result.exit_code == 0
        assert output_of(result).count("cli-demo") == 2

    def test_history_workflow_filter(self, tmp_path: Path):
        config = make_config(tmp_path, OK_CONFIG)
        db = str(tmp_path / "h.db")
        runner.invoke(app, ["run", str(config), "--db", db])
        result = runner.invoke(app, ["history", "--db", db, "--workflow", "other"])
        assert "No runs" in output_of(result)

    def test_show_displays_run_detail(self, tmp_path: Path):
        config = make_config(tmp_path, FAIL_CONFIG)
        db = str(tmp_path / "h.db")
        runner.invoke(app, ["run", str(config), "--db", db])

        from flowguard.storage.database import Database

        record = Database(db).list_runs()[0]
        history = output_of(runner.invoke(app, ["history", "--db", db]))
        assert record["run_id"] in history

        result = runner.invoke(app, ["show", record["run_id"], "--db", db])
        assert result.exit_code == 0
        combined = output_of(result)
        assert "cli-fail" in combined
        assert "boom" in combined
        assert "skipped" in combined

    def test_show_unknown_run_fails(self, tmp_path: Path):
        result = runner.invoke(app, ["show", "deadbeef", "--db", str(tmp_path / "h.db")])
        assert result.exit_code == 1


class TestMisc:
    def test_version_flag(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "flowguard" in output_of(result)

    def test_help_lists_required_commands(self):
        result = runner.invoke(app, ["--help"])
        combined = output_of(result)
        for command in ("init", "validate", "run", "history"):
            assert command in combined

    def test_entry_point_callable(self):
        from flowguard.cli.commands import main

        assert callable(main)
