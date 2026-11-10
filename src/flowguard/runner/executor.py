"""Subprocess execution of workflow steps.

Runs a step's command as a local subprocess and captures stdout, stderr,
the exit code, and the duration. Works uniformly on Windows, Linux, and
macOS. Command dispatch rules:

* a string command runs through the platform shell (``cmd.exe`` on
  Windows, ``/bin/sh`` elsewhere);
* a list command is dispatched directly, without a shell — except a
  single-element list containing a full command line such as
  ``["npm install"]``, which could only ever fail as a single argv token
  and is therefore routed through the shell.

Timeouts terminate the *whole process tree*, not just the direct child.
Shell commands spawn grandchildren, and killing only ``cmd.exe`` or
``/bin/sh`` leaves the real work running (and holding the output pipes
open). POSIX uses a dedicated process group; Windows uses ``taskkill /T``.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from flowguard.core.models import Status, Step, StepResult

__all__ = ["CommandExecutor"]


def _popen_kwargs() -> dict[str, object]:
    """Platform-specific options that let us kill a whole process tree."""
    if os.name == "posix":
        # The child becomes the leader of a fresh process group, so
        # killpg() reaches the shell *and* every descendant it spawned.
        return {"start_new_session": True}
    return {}


def _kill_process_tree(process: subprocess.Popen[str]) -> None:
    """Terminate *process* and every descendant as forcefully as possible."""
    if os.name == "posix":
        with suppress(ProcessLookupError, PermissionError, OSError):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    else:
        # taskkill /T walks the process tree rooted at the shell. It is
        # best-effort: the direct kill below still handles the simple case
        # (e.g. when taskkill is unavailable in a stripped container).
        with suppress(OSError, subprocess.SubprocessError):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
    with suppress(OSError):
        process.kill()


def _close_pipes(process: subprocess.Popen[str]) -> None:
    for pipe in (process.stdout, process.stderr):
        if pipe is not None:
            with suppress(OSError):
                pipe.close()


def _prepare_command(command: str | list[str]) -> tuple[str | list[str], bool]:
    """Return (dispatch argument, use_shell) for *command*."""
    if isinstance(command, str):
        return command, True
    if len(command) == 1 and any(char.isspace() for char in command[0]):
        # A whole command line was pasted as a one-element list, e.g.
        # ["npm install"]; it needs a shell to be meaningful.
        return command[0], True
    return list(command), False


class CommandExecutor:
    """Executes a single :class:`Step` command and captures its outcome."""

    def execute(
        self,
        step: Step,
        *,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        timeout: float | None = None,
    ) -> StepResult:
        """Run *step* and return a :class:`StepResult`.

        Arguments:
            step: The step whose command should run.
            env: Complete environment for the child process. When ``None``,
                the parent environment is inherited.
            cwd: Working directory for the child process. Falls back to
                ``step.working_dir`` and then to the current directory.
            timeout: Wall-clock limit in seconds; defaults to the step's
                own ``timeout``. On expiry the process group is killed and
                the result status is :attr:`Status.TIMEOUT`.

        The returned result always records ``stdout``, ``stderr``, exit
        code (when the process was started), duration, and timestamps.
        A command that cannot be started at all (missing binary, bad
        working directory) yields :attr:`Status.FAILED` with a descriptive
        ``error`` message rather than raising.
        """
        effective_timeout = timeout if timeout is not None else step.timeout
        effective_cwd = str(cwd) if cwd is not None else step.working_dir
        dispatch_command, use_shell = _prepare_command(step.command)

        started_at = time.time()
        try:
            process = subprocess.Popen(
                dispatch_command,
                shell=use_shell,
                cwd=effective_cwd,
                env=env if env is not None else build_environment(step.environment),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_popen_kwargs(),
            )
        except OSError as exc:
            finished_at = time.time()
            return StepResult(
                step_name=step.name,
                status=Status.FAILED,
                error=f"failed to start command: {exc}",
                duration_ms=(finished_at - started_at) * 1000.0,
                started_at=started_at,
                finished_at=finished_at,
            )

        timed_out = False
        stdout, stderr = "", ""
        try:
            stdout, stderr = process.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            # Whatever the child produced before being killed is still
            # useful for diagnosing the timeout.
            stdout, stderr = exc.stdout or "", exc.stderr or ""
            if isinstance(stdout, bytes):
                stdout = stdout.decode("utf-8", errors="replace")
            if isinstance(stderr, bytes):
                stderr = stderr.decode("utf-8", errors="replace")
            _kill_process_tree(process)
            try:
                captured_stdout, captured_stderr = process.communicate(timeout=2)
                stdout += captured_stdout or ""
                stderr += captured_stderr or ""
            except (subprocess.TimeoutExpired, OSError, ValueError):
                # Pipes may be held by a detached descendant. Close them so
                # the executor never blocks past the configured timeout.
                _close_pipes(process)
                with suppress(OSError, subprocess.TimeoutExpired):
                    process.wait(timeout=1)

        finished_at = time.time()
        exit_code = process.returncode

        if timed_out:
            status = Status.TIMEOUT
            error = f"command timed out after {effective_timeout} second(s) and was terminated"
        elif exit_code == 0:
            status = Status.SUCCEEDED
            error = None
        else:
            status = Status.FAILED
            error = f"command exited with code {exit_code}"

        return StepResult(
            step_name=step.name,
            status=status,
            exit_code=exit_code,
            stdout=stdout or "",
            stderr=stderr or "",
            error=error,
            duration_ms=(finished_at - started_at) * 1000.0,
            attempts=1,
            started_at=started_at,
            finished_at=finished_at,
        )


def build_environment(
    *layers: dict[str, str] | None,
    base: dict[str, str] | None = None,
) -> dict[str, str]:
    """Merge environment *layers* over ``base`` (defaults to the parent env).

    Later layers win. ``None`` layers are skipped. Used by the engine to
    combine the parent environment, workflow-level and step-level
    ``environment`` blocks, and CLI ``--env`` overrides.
    """
    merged = (base if base is not None else os.environ).copy()
    for layer in layers:
        if layer:
            merged.update(layer)
    return merged


def shell_hint() -> str:
    """Return the shell used for string commands on this platform."""
    return "cmd.exe" if sys.platform == "win32" else "/bin/sh"
