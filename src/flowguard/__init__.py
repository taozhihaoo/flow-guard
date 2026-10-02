"""FlowGuard — a local-first workflow automation engine.

FlowGuard executes deterministic, dependency-aware workflows defined in a
``.flowguard.yml`` file. It runs entirely on your machine with no external
services: steps execute as local subprocesses, results are stored in SQLite,
and an optional REST API exposes run history.
"""

from flowguard.core.engine import ExecutionEngine
from flowguard.core.exceptions import (
    ConfigError,
    CycleError,
    FlowGuardError,
    SchedulerError,
    UnknownDependencyError,
)
from flowguard.core.models import (
    PipelineResult,
    RetryPolicy,
    Status,
    Step,
    StepResult,
    Workflow,
)
from flowguard.core.scheduler import DAG

__version__ = "0.1.0"

__all__ = [
    "DAG",
    "ConfigError",
    "CycleError",
    "ExecutionEngine",
    "FlowGuardError",
    "PipelineResult",
    "RetryPolicy",
    "SchedulerError",
    "Status",
    "Step",
    "StepResult",
    "UnknownDependencyError",
    "Workflow",
    "__version__",
]
