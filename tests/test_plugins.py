"""Tests for the plugin architecture (Phase 11)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pytest

from flowguard.core.models import Status, StepResult
from flowguard.plugins import LoggingPlugin, Plugin, PluginError, PluginManager
from flowguard.plugins import manager as manager_module
from plugin_sample import SamplePlugin


class RecordingPlugin(Plugin):
    name = "recording"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def before_pipeline(self, context) -> None:
        self.calls.append(("before_pipeline", context.run_id, context.workflow_name))

    def before_step(self, context, step) -> None:
        self.calls.append(("before_step", step.name))

    def after_step(self, context, step, result) -> None:
        self.calls.append(("after_step", step.name, result.status.value))

    def after_pipeline(self, context, result) -> None:
        self.calls.append(("after_pipeline", result.status.value))


class TestRegistry:
    def test_register_and_inspect(self):
        manager = PluginManager()
        plugin = RecordingPlugin()
        manager.register(plugin)
        assert manager.plugins == [plugin]
        assert len(manager) == 1

    def test_register_rejects_non_plugin(self):
        manager = PluginManager()
        with pytest.raises(PluginError, match="not a FlowGuard plugin"):
            manager.register(object())  # type: ignore[arg-type]

    def test_empty_manager_noop_hooks(self):
        manager = PluginManager()
        assert manager.before_pipeline(context_stub()) == []
        assert manager.after_pipeline(context_stub(), result_stub()) == []


class TestHookDispatch:
    def test_all_four_hooks_receive_expected_data(self):
        manager = PluginManager()
        plugin = RecordingPlugin()
        manager.register(plugin)

        context = context_stub(run_id="r9", workflow_name="wf")
        manager.before_pipeline(context)
        manager.before_step(context, step_stub("build"))
        manager.after_step(context, step_stub("build"), result_stub(Status.SUCCEEDED))
        manager.after_pipeline(context, result_stub(Status.FAILED))

        assert plugin.calls == [
            ("before_pipeline", "r9", "wf"),
            ("before_step", "build"),
            ("after_step", "build", "succeeded"),
            ("after_pipeline", "failed"),
        ]

    def test_multiple_plugins_all_called(self):
        manager = PluginManager()
        first, second = RecordingPlugin(), RecordingPlugin()
        manager.register(first)
        manager.register(second)
        context = context_stub()
        manager.before_pipeline(context)
        assert first.calls and second.calls

    def test_hook_exception_is_captured_not_raised(self):
        class Exploder(Plugin):
            name = "exploder"

            def before_pipeline(self, context) -> None:
                raise ValueError("hook blew up")

        manager = PluginManager()
        manager.register(Exploder())
        errors = manager.before_pipeline(context_stub())
        assert len(errors) == 1
        assert "exploder" in errors[0]
        assert "hook blew up" in errors[0]

    def test_healthy_plugin_unaffected_by_broken_one(self):
        manager = PluginManager()
        healthy = RecordingPlugin()
        manager.register(healthy)

        class Exploder(Plugin):
            name = "exploder"

            def before_pipeline(self, context) -> None:
                raise RuntimeError("nope")

        manager.register(Exploder())
        manager.before_pipeline(context_stub())
        assert healthy.calls  # healthy plugin still ran


class TestSpecLoading:
    def test_load_from_spec_class(self):
        manager = PluginManager()
        plugin = manager.load_from_spec("plugin_sample:SamplePlugin")
        assert isinstance(plugin, SamplePlugin)
        assert plugin in manager.plugins

    def test_load_from_spec_instance(self):
        from plugin_sample import sample_instance

        manager = PluginManager()
        plugin = manager.load_from_spec("plugin_sample:sample_instance")
        assert plugin is sample_instance

    def test_load_from_spec_missing_module(self):
        manager = PluginManager()
        with pytest.raises(PluginError, match="Cannot import plugin module"):
            manager.load_from_spec("no_such_module_xyz:Plugin")

    def test_load_from_spec_missing_attribute(self):
        manager = PluginManager()
        with pytest.raises(PluginError, match="not found"):
            manager.load_from_spec("plugin_sample:DoesNotExist")

    def test_load_from_spec_not_a_plugin(self):
        manager = PluginManager()
        with pytest.raises(PluginError, match="does not resolve"):
            manager.load_from_spec("plugin_sample:sample_instance.calls")

    def test_load_from_spec_broken_constructor(self):
        manager = PluginManager()
        with pytest.raises(PluginError, match="Cannot instantiate"):
            manager.load_from_spec("plugin_sample:BrokenPlugin")

    def test_load_from_spec_requires_colon(self):
        manager = PluginManager()
        with pytest.raises(PluginError, match="PluginClass"):
            manager.load_from_spec("plugin_sample.SamplePlugin")


class TestEntryPointLoading:
    def test_entry_points_loaded(self, monkeypatch):
        plugin = RecordingPlugin()

        @dataclass
        class FakeEP:
            name: str
            value: str = "x:y"

            def load(self) -> Any:
                return type(plugin)

        monkeypatch.setattr(
            manager_module,
            "entry_points",
            lambda group: [FakeEP("recording")] if group == "flowguard.plugins" else [],
        )
        manager = PluginManager()
        loaded = manager.load_entry_points()
        assert loaded == ["recording"]
        assert manager.plugins[0].calls == []

    def test_broken_entry_point_skipped(self, monkeypatch, caplog):
        @dataclass
        class BadEP:
            name: str
            value: str = "x:y"

            def load(self) -> Any:
                raise ImportError("package is broken")

        monkeypatch.setattr(manager_module, "entry_points", lambda group: [BadEP("bad")])
        manager = PluginManager()
        with caplog.at_level(logging.WARNING):
            assert manager.load_entry_points() == []
        assert manager.plugins == []
        assert any("broken" in record.message for record in caplog.records)


class TestLoggingPlugin:
    def test_logs_pipeline_events(self, caplog):
        plugin = LoggingPlugin()
        context = context_stub(run_id="run-1", workflow_name="demo")
        with caplog.at_level(logging.INFO, logger="flowguard.pipeline"):
            plugin.before_pipeline(context)
            plugin.before_step(context, step_stub("a"))
            plugin.after_step(context, step_stub("a"), result_stub(Status.SUCCEEDED))
            plugin.after_pipeline(context, result_stub(Status.SUCCEEDED))
        messages = [record.message for record in caplog.records]
        assert any("demo" in message and "run-1" in message for message in messages)
        assert any("step 'a' starting" in message for message in messages)
        assert any("step 'a' finished: succeeded" in message for message in messages)


# -- helpers ------------------------------------------------------------------


def context_stub(**kwargs: Any):
    from flowguard.plugins.base import PipelineContext

    defaults: dict[str, Any] = {"run_id": "r1", "workflow_name": "wf"}
    defaults.update(kwargs)
    return PipelineContext(**defaults)


def step_stub(name: str):
    from flowguard.core.models import Step

    return Step(name=name, command=["echo", name])


def result_stub(status: Status = Status.SUCCEEDED):
    return StepResult(step_name="a", status=status)
