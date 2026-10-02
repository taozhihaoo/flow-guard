"""Plugin architecture for FlowGuard.

A plugin observes (and may enrich) pipeline execution by implementing any
subset of four hooks:

* ``before_pipeline(context)`` — once, before the first step runs.
* ``before_step(context, step)`` — before each individual step.
* ``after_step(context, step, result)`` — after each individual step.
* ``after_pipeline(context, result)`` — once, after the run completes.

Hooks are plain synchronous methods; a hook that raises does not abort the
pipeline — the error is recorded and the run continues. See
:mod:`flowguard.plugins.manager` for registration and discovery.
"""

from flowguard.plugins.base import PipelineContext, Plugin, PluginError
from flowguard.plugins.builtin import LoggingPlugin
from flowguard.plugins.manager import PluginManager

__all__ = [
    "LoggingPlugin",
    "PipelineContext",
    "Plugin",
    "PluginError",
    "PluginManager",
]
