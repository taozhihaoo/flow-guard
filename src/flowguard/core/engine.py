"""Workflow execution engine.

The engine walks the dependency graph produced by
:mod:`flowguard.core.scheduler` and runs every step whose dependencies have
succeeded. Independent steps execute concurrently: the asyncio event loop
dispatches blocking subprocess work to a
:class:`concurrent.futures.ThreadPoolExecutor`, so a step's retry loop,
timeout handling, and artifact collection never block other steps.

Failure semantics (deterministic, CI-like):

* A step whose dependencies did not succeed is marked ``skipped`` — never
  executed.
* Sibling branches are unaffected: a failure skips only its own
  descendants.
* The pipeline succeeds only when every step succeeded.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from flowguard.core.exceptions import SchedulerError
from flowguard.core.models import (
    PipelineResult,
    Status,
    Step,
    StepResult,
    Workflow,
)
from flowguard.core.scheduler import DAG
from flowguard.plugins.base import PipelineContext
from flowguard.plugins.manager import PluginManager
from flowguard.runner.executor import CommandExecutor, build_environment
from flowguard.runner.retry import RetryExecutor
from flowguard.storage.artifacts import ArtifactCollector
from flowguard.storage.database import Database

__all__ = ["ExecutionEngine"]


class ExecutionEngine:
    """Executes a :class:`Workflow` and records the run.

    Arguments:
        storage: Optional :class:`Database`; when given, the run and each
            step result are persisted.
        plugins: Optional :class:`PluginManager` whose hooks are invoked
            around the pipeline and each step.
        max_workers: Upper bound on concurrently executing steps. ``1``
            serialises the whole pipeline.
        artifact_root: Root directory for collected artifacts. Defaults to
            ``<config_dir>/.flowguard/artifacts`` the first time a step
            declares artifacts.
    """

    def __init__(
        self,
        *,
        storage: Database | None = None,
        plugins: PluginManager | None = None,
        max_workers: int | None = None,
        artifact_root: Path | None = None,
    ) -> None:
        self.storage = storage
        self.plugins = plugins if plugins is not None else PluginManager()
        self.artifact_root = artifact_root
        self._max_workers = max_workers
        self._pool: ThreadPoolExecutor | None = None

    # -- public API ----------------------------------------------------------

    async def run(
        self,
        workflow: Workflow,
        *,
        config_dir: Path | None = None,
        env_overrides: dict[str, str] | None = None,
        run_id: str | None = None,
    ) -> PipelineResult:
        """Execute *workflow* and return the aggregated result.

        ``config_dir`` is the directory containing the workflow file; it
        anchors relative ``working_dir`` values and the default artifact
        root. ``env_overrides`` win over workflow- and step-level
        environment entries.
        """
        dag = DAG.from_workflow(workflow)
        dag.topological_order()  # fail fast on cycles before anything runs

        base_dir = Path(config_dir) if config_dir is not None else Path.cwd()
        run_id = run_id or uuid.uuid4().hex[:12]
        started_at = time.time()

        context = PipelineContext(
            run_id=run_id,
            workflow_name=workflow.name,
            config_dir=str(base_dir),
            started_at=started_at,
        )

        self._pool = ThreadPoolExecutor(
            max_workers=self._max_workers, thread_name_prefix="flowguard-step"
        )
        try:
            if self.storage is not None:
                self.storage.create_run(
                    run_id,
                    workflow.name,
                    config_path=str(base_dir),
                    total_steps=len(workflow.steps),
                    started_at=started_at,
                )
            self._record_event(run_id, f"run started: workflow '{workflow.name}'")
            for message in self.plugins.before_pipeline(context):
                self._record_event(run_id, message, level="warning")

            results = await self._schedule(dag, workflow, context, base_dir, env_overrides)

            finished_at = time.time()
            overall = (
                Status.SUCCEEDED
                if all(result.succeeded for result in results.values())
                else Status.FAILED
            )
            pipeline_result = PipelineResult(
                run_id=run_id,
                workflow_name=workflow.name,
                status=overall,
                step_results=[
                    results[step.name] for step in workflow.steps if step.name in results
                ],
                started_at=started_at,
                finished_at=finished_at,
                duration_ms=(finished_at - started_at) * 1000.0,
            )

            for message in self.plugins.after_pipeline(context, pipeline_result):
                self._record_event(run_id, message, level="warning")

            if self.storage is not None:
                self.storage.update_run(
                    run_id,
                    status=overall,
                    finished_at=finished_at,
                    duration_ms=pipeline_result.duration_ms,
                )
            self._record_event(
                run_id,
                f"run finished: {overall.value} in {pipeline_result.duration_ms:.0f} ms",
            )
            return pipeline_result
        finally:
            self._pool.shutdown(wait=False, cancel_futures=True)
            self._pool = None

    # -- scheduling ------------------------------------------------------------

    async def _schedule(
        self,
        dag: DAG,
        workflow: Workflow,
        context: PipelineContext,
        base_dir: Path,
        env_overrides: dict[str, str] | None,
    ) -> dict[str, StepResult]:
        """Drive the DAG to completion, returning per-step results."""
        steps_by_name = {step.name: step for step in workflow.steps}
        declaration_order = {name: i for i, name in enumerate(workflow.step_names)}
        results: dict[str, StepResult] = {}
        pending = set(workflow.step_names)

        while pending:
            failed_deps = {
                name
                for name in pending
                if any(
                    dep in results and not results[dep].succeeded for dep in dag.dependencies(name)
                )
            }
            if failed_deps:
                for name in sorted(failed_deps, key=declaration_order.__getitem__):
                    blocking = [
                        dep
                        for dep in dag.dependencies(name)
                        if dep in results and not results[dep].succeeded
                    ]
                    results[name] = StepResult(
                        step_name=name,
                        status=Status.SKIPPED,
                        error="skipped because dependency "
                        + ", ".join(repr(dep) for dep in blocking)
                        + " did not succeed",
                        started_at=context.started_at,
                        finished_at=time.time(),
                    )
                    self._record_event(
                        context.run_id, f"step '{name}' skipped ({results[name].error})"
                    )
                    if self.storage is not None:
                        self.storage.add_step_result(context.run_id, results[name])
                    pending.discard(name)
                continue

            ready = [
                steps_by_name[name]
                for name in workflow.step_names
                if name in pending and all(dep in results for dep in dag.dependencies(name))
            ]
            if not ready:  # pragma: no cover - cycles are rejected in run()
                raise SchedulerError(
                    "no runnable steps but work remains — this indicates a "
                    "scheduler bug or a mutated workflow"
                )

            batch = await asyncio.gather(
                *(
                    self._run_step(step, context, workflow, base_dir, env_overrides)
                    for step in ready
                )
            )
            for result in batch:
                results[result.step_name] = result
                pending.discard(result.step_name)
                self._record_event(
                    context.run_id,
                    f"step '{result.step_name}' finished: {result.status.value} "
                    f"(attempt {result.attempts}, {result.duration_ms:.0f} ms)",
                )
                if self.storage is not None:
                    self.storage.add_step_result(context.run_id, result)

        return results

    # -- single step ---------------------------------------------------------------

    async def _run_step(
        self,
        step: Step,
        context: PipelineContext,
        workflow: Workflow,
        base_dir: Path,
        env_overrides: dict[str, str] | None,
    ) -> StepResult:
        """Run one step (with retries) in the thread pool."""
        for message in self.plugins.before_step(context, step):
            self._record_event(context.run_id, message, level="warning")

        loop = asyncio.get_running_loop()
        assert self._pool is not None
        result = await loop.run_in_executor(
            self._pool,
            self._execute_step_blocking,
            step,
            context,
            workflow,
            base_dir,
            env_overrides,
        )

        for message in self.plugins.after_step(context, step, result):
            self._record_event(context.run_id, message, level="warning")
        return result

    def _execute_step_blocking(
        self,
        step: Step,
        context: PipelineContext,
        workflow: Workflow,
        base_dir: Path,
        env_overrides: dict[str, str] | None,
    ) -> StepResult:
        """Blocking part: subprocess + retry loop + artifacts (runs in a worker thread).

        Environment layers, from weakest to strongest: parent process,
        workflow-level, step-level, CLI overrides.
        """
        working_dir = self._resolve_working_dir(step, base_dir)
        environment = build_environment(
            workflow.environment,
            step.environment,
            env_overrides,
        )

        executor = CommandExecutor()
        retries = RetryExecutor()

        def attempt() -> StepResult:
            return executor.execute(step, env=environment, cwd=working_dir)

        result = retries.run(attempt, step.retry)

        collector = self._ensure_artifact_collector(base_dir) if step.artifacts else None
        if collector is not None:
            try:
                collected = collector.collect(
                    context.run_id, step.name, step.artifacts, cwd=working_dir
                )
                result.artifacts = collected
            except OSError as exc:
                result.error = (result.error + "; " if result.error else "") + (
                    f"artifact collection failed: {exc}"
                )
            collector.save_step_logs(context.run_id, result)
        return result

    def _resolve_working_dir(self, step: Step, base_dir: Path) -> Path:
        if step.working_dir is None:
            return base_dir
        candidate = Path(step.working_dir)
        return candidate if candidate.is_absolute() else base_dir / candidate

    def _ensure_artifact_collector(self, base_dir: Path) -> ArtifactCollector:
        if self.artifact_root is not None:
            return ArtifactCollector(self.artifact_root)
        return ArtifactCollector(base_dir / ".flowguard" / "artifacts")

    def _record_event(self, run_id: str, message: str, *, level: str = "info") -> None:
        if self.storage is not None:
            self.storage.add_event(run_id, message, level=level)
