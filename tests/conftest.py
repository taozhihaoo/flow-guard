"""Shared fixtures and helpers for the FlowGuard test suite."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from flowguard.config.parser import parse_config
from flowguard.core.models import Step, Workflow

#: Absolute path of the current interpreter — subprocess tests use it so the
#: suite behaves identically on Windows, Linux, and macOS.
PY = sys.executable


@pytest.fixture()
def py() -> str:
    """Path to the running Python interpreter for child processes."""
    return sys.executable


@pytest.fixture()
def tmp_project(tmp_path: Path) -> Path:
    """An empty per-test project directory."""
    return tmp_path


@pytest.fixture()
def write_config(tmp_path: Path):
    """Factory: write YAML text into the temp project and return its path."""

    def _write(content: str, name: str = ".flowguard.yml") -> Path:
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        return path

    return _write


@pytest.fixture()
def make_workflow():
    """Factory: build a :class:`Workflow` programmatically.

    Accepts either :class:`Step` instances or raw dictionaries.
    """

    def _make(
        *steps: Step | dict[str, Any],
        name: str = "test-workflow",
        environment: dict[str, str] | None = None,
    ) -> Workflow:
        normalised: list[dict[str, Any]] = []
        for step in steps:
            if isinstance(step, Step):
                step = step.model_dump()
            normalised.append(step)
        config: dict[str, Any] = {"workflow": {"name": name, "steps": normalised}}
        if environment:
            config["workflow"]["environment"] = environment
        return parse_config(config)

    return _make


def python_step(name: str, code: str, **extra: Any) -> dict[str, Any]:
    """A step running ``python -c <code>`` with the current interpreter."""
    step: dict[str, Any] = {
        "name": name,
        "command": [sys.executable, "-c", code],
        **extra,
    }
    return step
