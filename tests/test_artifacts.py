"""Tests for artifact collection (Phase 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from flowguard.core.models import Status, StepResult
from flowguard.storage.artifacts import ArtifactCollector


def make_result(name: str = "build", **extra: object) -> StepResult:
    extra.setdefault("stdout", "log output")
    return StepResult(
        step_name=name,
        status=Status.SUCCEEDED,
        **extra,  # type: ignore[arg-type]
    )


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    # A fake build output tree.
    (tmp_path / "dist").mkdir()
    (tmp_path / "dist" / "app.bin").write_text("binary", encoding="utf-8")
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "junit.xml").write_text("<xml/>", encoding="utf-8")
    (tmp_path / "reports" / "cov.xml").write_text("<xml/>", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "deep").mkdir()
    (tmp_path / "src" / "deep" / "note.txt").write_text("hi", encoding="utf-8")
    return tmp_path


class TestCollect:
    def test_single_file_artifact(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        copied = collector.collect("r1", "build", ["dist/app.bin"], cwd=project)
        assert copied == ["dist/app.bin"]
        saved = tmp_path / "artifacts" / "r1" / "build" / "dist" / "app.bin"
        assert saved.read_text(encoding="utf-8") == "binary"

    def test_glob_pattern(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        copied = collector.collect("r1", "build", ["reports/*.xml"], cwd=project)
        assert sorted(copied) == ["reports/cov.xml", "reports/junit.xml"]
        assert (tmp_path / "artifacts" / "r1" / "build" / "reports" / "junit.xml").exists()

    def test_directory_artifact_copied_recursively(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        copied = collector.collect("r1", "build", ["src"], cwd=project)
        assert copied == ["src"]
        deep = tmp_path / "artifacts" / "r1" / "build" / "src" / "deep" / "note.txt"
        assert deep.read_text(encoding="utf-8") == "hi"

    def test_no_match_is_not_an_error(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        assert collector.collect("r1", "build", ["nope/*.zz"], cwd=project) == []

    def test_relative_paths_preserved(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        copied = collector.collect("r1", "build", ["src/deep/note.txt"], cwd=project)
        assert copied == ["src/deep/note.txt"]

    def test_step_directories_are_separate(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        collector.collect("r1", "one", ["dist/app.bin"], cwd=project)
        collector.collect("r1", "two", ["reports/junit.xml"], cwd=project)
        assert (tmp_path / "artifacts" / "r1" / "one" / "dist" / "app.bin").exists()
        assert (tmp_path / "artifacts" / "r1" / "two" / "reports" / "junit.xml").exists()

    def test_runs_are_separate(self, tmp_path: Path, project: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        collector.collect("r1", "build", ["dist/app.bin"], cwd=project)
        collector.collect("r2", "build", ["dist/app.bin"], cwd=project)
        assert (tmp_path / "artifacts" / "r1" / "build").exists()
        assert (tmp_path / "artifacts" / "r2" / "build").exists()


class TestLogs:
    def test_step_log_written(self, tmp_path: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        path = collector.save_step_logs("r1", make_result(stdout="hello"))
        content = path.read_text(encoding="utf-8")
        assert "hello" in content
        assert "succeeded" in content

    def test_log_contains_stderr_and_error(self, tmp_path: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        result = StepResult(
            step_name="broken",
            status=Status.FAILED,
            stderr="stacktrace...",
            error="command exited with code 1",
        )
        path = collector.save_step_logs("r1", result)
        content = path.read_text(encoding="utf-8")
        assert "stacktrace..." in content
        assert "command exited with code 1" in content

    def test_log_directory_layout(self, tmp_path: Path):
        collector = ArtifactCollector(tmp_path / "artifacts")
        collector.save_step_logs("run-42", make_result(name="compile"))
        assert (tmp_path / "artifacts" / "run-42" / "logs" / "compile.log").is_file()
