"""Built-in plugins shipped with FlowGuard."""

from __future__ import annotations

import logging

from flowguard.core.models import PipelineResult, Step, StepResult
from flowguard.plugins.base import PipelineContext, Plugin

__all__ = ["LoggingPlugin"]

logger = logging.getLogger("flowguard.pipeline")


class LoggingPlugin(Plugin):
    """Emits a structured log line for every pipeline event.

    Register it with a :class:`PluginManager` (or rely on default engine
    wiring) and configure standard-library logging to see pipeline progress.
    """

    name = "logging"

    def before_pipeline(self, context: PipelineContext) -> None:
        logger.info(f"pipeline '{context.workflow_name}' starting (run {context.run_id})")

    def before_step(self, context: PipelineContext, step: Step) -> None:
        logger.info(f"[{context.run_id}] step '{step.name}' starting")

    def after_step(self, context: PipelineContext, step: Step, result: StepResult) -> None:
        logger.info(
            f"[{context.run_id}] step '{step.name}' finished: "
            f"{result.status.value} ({result.duration_ms:.0f} ms, "
            f"{result.attempts} attempt(s))"
        )

    def after_pipeline(self, context: PipelineContext, result: PipelineResult) -> None:
        logger.info(
            f"[{context.run_id}] pipeline '{context.workflow_name}' finished: "
            f"{result.status.value} in {result.duration_ms:.0f} ms"
        )
