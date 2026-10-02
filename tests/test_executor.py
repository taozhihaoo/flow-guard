"""Tests for subprocess command execution (Phase 3)."""

from __future__ import annotations

import sys
import time

import pytest

from flowguard.core.models import Status, Step
from flowguard.runner.executor import CommandExecutor, build_environment, shell_hint

pytestmark = pytest.mark.usefixtures("py")


def run(step: Step, **kwargs: object):
    return CommandExecutor().execute(step, **kwargs)  # type: ignore[arg-type]


class TestCapture:
    def test_successful_command(self, py: str):
        result = run(Step(name="ok", command=[py, "-c", "print('hello')"]))
        assert result.status is Status.SUCCEEDED
        assert result.exit_code == 0
        assert result.stdout.strip() == "hello"
        assert result.stderr == ""
        assert result.error is None

    def test_failing_exit_code_captured(self, py: str):
        result = run(Step(name="fail", command=[py, "-c", "raise SystemExit(3)"]))
        assert result.status is Status.FAILED
        assert result.exit_code == 3
        assert "exited with code 3" in (result.error or "")

    def test_stderr_captured(self, py: str):
        result = run(
            Step(name="err", command=[py, "-c", "import sys; print('bad', file=sys.stderr)"])
        )
        assert "bad" in result.stderr

    def test_duration_and_timestamps_recorded(self, py: str):
        result = run(Step(name="t", command=[py, "-c", "pass"]))
        assert result.duration_ms >= 0
        assert result.started_at > 0
        assert result.finished_at >= result.started_at
        assert result.attempts == 1

    def test_duration_reflects_sleep(self, py: str):
        result = run(Step(name="sleepy", command=[py, "-c", "import time; time.sleep(0.3)"]))
        assert result.duration_ms >= 250

    def test_multiline_output_complete(self, py: str):
        code = "print('one'); print('two'); print('three')"
        result = run(Step(name="ml", command=[py, "-c", code]))
        assert result.stdout.split() == ["one", "two", "three"]

    def test_unicode_output(self, py: str):
        result = run(
            Step(
                name="uni",
                command=[py, "-c", "print('héllo ✓')"],
                environment={"PYTHONIOENCODING": "utf-8"},
            )
        )
        assert "héllo" in result.stdout


class TestCommandForms:
    def test_list_command_runs_without_shell(self, py: str):
        result = run(Step(name="lst", command=[py, "-c", "print('direct')"]))
        assert result.succeeded

    def test_string_command_runs_in_shell(self):
        result = run(Step(name="shell", command="echo via-shell"))
        assert result.succeeded
        assert "via-shell" in result.stdout

    def test_single_token_command_line_runs_in_shell(self):
        # The documented `command: [npm install]` style: a one-element list
        # holding a full command line is executed through the shell.
        result = run(Step(name="line", command=["echo pasted-line"]))
        assert result.succeeded
        assert "pasted-line" in result.stdout

    def test_multi_token_list_keeps_spaces_in_arguments(self, py: str):
        # A spaced token in a multi-token list is a single argument.
        result = run(
            Step(
                name="args",
                command=[py, "-c", "import sys; print(sys.argv[1])", "a b"],
            )
        )
        assert result.succeeded
        assert result.stdout.strip() == "a b"

    def test_shell_hint_matches_platform(self):
        expected = "cmd.exe" if sys.platform == "win32" else "/bin/sh"
        assert shell_hint() == expected


class TestEnvironment:
    def test_env_variable_reaches_child(self, py: str):
        step = Step(
            name="env",
            command=[py, "-c", "import os; print(os.environ['FLOWGUARD_TEST_VAR'])"],
            environment={"FLOWGUARD_TEST_VAR": "42"},
        )
        assert run(step).stdout.strip() == "42"

    def test_step_env_overrides_parent(self, py: str):
        step = Step(
            name="env",
            command=[py, "-c", "import os; print(os.environ['PATHOVERRIDE'])"],
            environment={"PATHOVERRIDE": "step-value"},
        )
        env = build_environment(step.environment, base={"PATHOVERRIDE": "parent"})
        result = run(step, env=env)
        assert result.stdout.strip() == "step-value"

    def test_layers_merge_in_order(self):
        merged = build_environment(
            {"A": "workflow", "B": "workflow"},
            {"B": "step", "C": "step"},
            {"C": "cli"},
            base={"A": "parent", "B": "parent", "C": "parent", "D": "parent"},
        )
        assert merged == {
            "A": "workflow",
            "B": "step",
            "C": "cli",
            "D": "parent",
        }

    def test_none_layers_skipped(self):
        merged = build_environment(None, {"A": "1"}, None, base={"B": "2"})
        assert merged == {"A": "1", "B": "2"}


class TestWorkingDirectory:
    def test_working_dir_used(self, py: str, tmp_path):
        result = run(
            Step(
                name="cwd",
                command=[py, "-c", "import os; print(os.getcwd())"],
                working_dir=str(tmp_path),
            )
        )
        assert Path_equal(result.stdout.strip(), tmp_path)

    def test_cwd_argument_beats_step_setting(self, py: str, tmp_path):
        result = run(
            Step(
                name="cwd",
                command=[py, "-c", "import os; print(os.getcwd())"],
                working_dir=str(tmp_path / "elsewhere"),
            ),
            cwd=tmp_path,
        )
        assert Path_equal(result.stdout.strip(), tmp_path)


def Path_equal(actual: str, expected) -> bool:
    from pathlib import Path

    return Path(actual).resolve() == Path(expected).resolve()


class TestTimeout:
    def test_timeout_status_and_error(self, py: str):
        result = run(
            Step(
                name="slow",
                command=[py, "-c", "import time; time.sleep(30)"],
                timeout=0.5,
            )
        )
        assert result.status is Status.TIMEOUT
        assert result.error is not None
        assert "timed out" in result.error
        assert "0.5" in result.error

    def test_timeout_actually_terminates(self, py: str):
        start = time.monotonic()
        result = run(
            Step(
                name="slow",
                command=[py, "-c", "import time; time.sleep(30)"],
                timeout=0.4,
            )
        )
        elapsed = time.monotonic() - start
        assert result.status is Status.TIMEOUT
        assert elapsed < 10  # killed promptly, not after the full sleep

    def test_timeout_override_parameter(self, py: str):
        # Step allows 60s, executor override cuts it to 0.3s.
        result = run(
            Step(
                name="slow",
                command=[py, "-c", "import time; time.sleep(30)"],
                timeout=60,
            ),
            timeout=0.3,
        )
        assert result.status is Status.TIMEOUT

    def test_no_timeout_runs_to_completion(self, py: str):
        result = run(Step(name="quick", command=[py, "-c", "import time; time.sleep(0.05)"]))
        assert result.status is Status.SUCCEEDED


class TestStartupFailures:
    def test_missing_binary_reports_failed_not_crash(self):
        result = run(
            Step(
                name="ghost",
                command=["definitely-not-a-real-binary-xyz", "--version"],
            )
        )
        assert result.status is Status.FAILED
        assert result.error is not None
        assert "failed to start" in result.error

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal exit codes")
    def test_negative_exit_code_on_posix(self, py: str):
        result = run(
            Step(
                name="sig",
                command=[py, "-c", "import os, signal; os.kill(os.getpid(), signal.SIGTERM)"],
            )
        )
        assert result.status is Status.FAILED
        assert result.exit_code == -15
