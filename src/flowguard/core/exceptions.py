"""Exception hierarchy for FlowGuard.

Every error raised intentionally by FlowGuard derives from
:class:`FlowGuardError`, so callers can catch a single base class.
Messages are written for humans: they name the offending file, step, or
field whenever that information is available.
"""

from __future__ import annotations

__all__ = [
    "ConfigError",
    "CycleError",
    "FlowGuardError",
    "SchedulerError",
    "UnknownDependencyError",
]


class FlowGuardError(Exception):
    """Base class for all FlowGuard errors."""


class ConfigError(FlowGuardError):
    """Raised when a workflow configuration is missing, unreadable, or invalid.

    The message is a multi-line, human-readable report pointing at the
    exact field or step that caused the problem.
    """


class SchedulerError(FlowGuardError):
    """Raised when the dependency graph cannot be scheduled."""


class UnknownDependencyError(SchedulerError):
    """Raised when a step depends on a step that does not exist."""


class CycleError(SchedulerError):
    """Raised when the dependency graph contains a cycle.

    The message includes the concrete cycle path, for example
    ``build -> test -> lint -> build``.
    """
