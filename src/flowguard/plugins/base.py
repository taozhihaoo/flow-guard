"""Plugin interface and execution context."""

from __future__ import annotations

from abc import ABC
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from flowguard.core.models import PipelineResult, Step, StepResult

__all__ = ["PipelineContext", "Plugin", "PluginError"]


class PluginError(Exception):
    """Raised when a plugin cannot be loaded or is not a valid plugin."""


class PipelineContext(BaseModel):
    """Information shared with plugins for the duration of one pipeline run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_name: str
    config_dir: str | None = None
    started_at: float = Field(default=0.0)


class Plugin(ABC):
    """Base class for FlowGuard plugins.

    Subclasses override only the hooks they care about; every hook defaults
    to a no-op so a minimal plugin can be an empty subclass.
    """

    #: Short identifier used in logs and error messages.
    name: ClassVar[str] = "plugin"

    def before_pipeline(self, context: PipelineContext) -> None:
        """Called once before the pipeline starts executing steps."""

    def before_step(self, context: PipelineContext, step: Step) -> None:
        """Called before *step* begins executing (skipped steps are not reported)."""

    def after_step(self, context: PipelineContext, step: Step, result: StepResult) -> None:
        """Called after *step* finishes with its :class:`StepResult`."""

    def after_pipeline(self, context: PipelineContext, result: PipelineResult) -> None:
        """Called once with the final :class:`PipelineResult`."""
