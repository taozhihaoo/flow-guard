"""Plugin registration, loading, and safe hook dispatch."""

from __future__ import annotations

import importlib
import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from flowguard.core.models import PipelineResult, Step, StepResult
from flowguard.plugins.base import PipelineContext, Plugin, PluginError

if TYPE_CHECKING:
    pass

__all__ = ["PluginManager"]

PLUGIN_ENTRY_POINT_GROUP = "flowguard.plugins"

logger = logging.getLogger("flowguard.plugins")

_HOOK_NAMES = ("before_pipeline", "before_step", "after_step", "after_pipeline")


class PluginManager:
    """Registers plugins and invokes their hooks defensively.

    A hook that raises is never allowed to abort a pipeline run: the error
    is logged and reported back to the engine as a string, which the engine
    records in the run's event log.
    """

    def __init__(self) -> None:
        self._plugins: list[Plugin] = []

    @property
    def plugins(self) -> list[Plugin]:
        return list(self._plugins)

    def __len__(self) -> int:
        return len(self._plugins)

    def register(self, plugin: Plugin) -> None:
        """Register a plugin instance."""
        if not isinstance(plugin, Plugin):
            raise PluginError(
                f"{type(plugin).__name__} is not a FlowGuard plugin "
                f"(subclass flowguard.plugins.Plugin)"
            )
        self._plugins.append(plugin)

    def load_entry_points(self) -> list[str]:
        """Discover and register plugins advertised via the
        ``flowguard.plugins`` entry-point group.

        Returns the names of the plugins that were loaded. Broken entry
        points are skipped with a log message rather than raised, so one
        bad third-party package cannot prevent FlowGuard from running.
        """
        loaded: list[str] = []
        for entry_point in entry_points(group=PLUGIN_ENTRY_POINT_GROUP):
            try:
                obj = entry_point.load()
                plugin = obj() if isinstance(obj, type) else obj
                self.register(plugin)
                loaded.append(getattr(plugin, "name", entry_point.name))
            except Exception as exc:
                logger.warning("Skipping broken FlowGuard plugin %r: %s", entry_point.name, exc)
        return loaded

    def load_from_spec(self, spec: str) -> Plugin:
        """Load a plugin from a ``module:Attribute`` specification string.

        Examples: ``mypackage.flowguard:SlackNotify`` or
        ``mypackage.flowguard:notify_plugin`` (a ready instance). The
        attribute may be a ``Plugin`` subclass (instantiated) or instance.
        """
        if ":" not in spec:
            raise PluginError(
                f"Invalid plugin specification {spec!r} — expected the form "
                f"'package.module:PluginClass'"
            )
        module_name, _, attribute_path = spec.partition(":")
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise PluginError(f"Cannot import plugin module {module_name!r}: {exc}") from exc
        obj: object = module
        for attribute in attribute_path.split("."):
            try:
                obj = getattr(obj, attribute)
            except AttributeError as exc:
                raise PluginError(
                    f"Plugin attribute {attribute!r} not found in {module_name!r}"
                ) from exc
        if isinstance(obj, type):
            try:
                obj = obj()
            except Exception as exc:
                raise PluginError(f"Cannot instantiate plugin {spec!r}: {exc}") from exc
        if not isinstance(obj, Plugin):
            raise PluginError(f"{spec!r} does not resolve to a FlowGuard plugin")
        self.register(obj)  # type: ignore[arg-type]
        return obj  # type: ignore[return-value]

    # -- hook dispatch -------------------------------------------------------

    def before_pipeline(self, context: PipelineContext) -> list[str]:
        return self._invoke("before_pipeline", context)

    def before_step(self, context: PipelineContext, step: Step) -> list[str]:
        return self._invoke("before_step", context, step)

    def after_step(self, context: PipelineContext, step: Step, result: StepResult) -> list[str]:
        return self._invoke("after_step", context, step, result)

    def after_pipeline(self, context: PipelineContext, result: PipelineResult) -> list[str]:
        return self._invoke("after_pipeline", context, result)

    def _invoke(self, hook: str, *args: object) -> list[str]:
        errors: list[str] = []
        for plugin in self._plugins:
            try:
                getattr(plugin, hook)(*args)
            except Exception as exc:
                message = f"plugin {plugin.name!r} hook {hook}() failed: {exc}"
                logger.warning(message)
                errors.append(message)
        return errors
