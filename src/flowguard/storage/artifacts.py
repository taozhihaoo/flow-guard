"""Collection of step artifacts (logs, reports, build outputs).

After a step finishes, glob patterns declared in the step's ``artifacts``
field are resolved against the step's working directory and copied into
``<artifact_root>/<run_id>/<step_name>/``, preserving relative paths.
Step logs (stdout, stderr, error text) are saved alongside as plain files
under ``<artifact_root>/<run_id>/logs/``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from flowguard.core.models import StepResult

__all__ = ["ArtifactCollector"]


class ArtifactCollector:
    """Copies declared artifacts and step logs into a run-scoped folder."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def run_dir(self, run_id: str) -> Path:
        return self.root / run_id

    def collect(
        self,
        run_id: str,
        step_name: str,
        patterns: list[str],
        *,
        cwd: Path,
    ) -> list[str]:
        """Copy files matching *patterns* into the run's artifact folder.

        Returns the list of copied paths relative to the step artifact
        directory. A pattern that matches nothing is not an error; a
        pattern that matches both files and directories copies both.
        """
        destination_root = self.run_dir(run_id) / step_name
        copied: list[str] = []
        for pattern in patterns:
            for match in sorted(cwd.glob(pattern)):
                relative = match.relative_to(cwd)
                target = destination_root / relative
                if match.is_dir():
                    shutil.copytree(match, target, dirs_exist_ok=True)
                elif match.is_file():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(match, target)
                else:  # pragma: no cover - glob rarely yields specials
                    continue
                copied.append(relative.as_posix())
        return copied

    def save_step_logs(self, run_id: str, result: StepResult) -> Path:
        """Write *result*'s captured output to ``logs/<step>.log``."""
        logs_dir = self.run_dir(run_id) / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_path = logs_dir / f"{result.step_name}.log"
        sections = [
            f"step: {result.step_name}",
            f"status: {result.status.value}",
            f"exit code: {result.exit_code if result.exit_code is not None else 'n/a'}",
            f"attempts: {result.attempts}",
            f"duration ms: {result.duration_ms:.1f}",
        ]
        if result.error:
            sections.append(f"error: {result.error}")
        body = "\n".join(sections)
        output = ""
        if result.stdout:
            output += f"\n--- stdout ---\n{result.stdout}"
        if result.stderr:
            output += f"\n--- stderr ---\n{result.stderr}"
        log_path.write_text(body + output + "\n", encoding="utf-8")
        return log_path
