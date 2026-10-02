"""A sample plugin module used by plugin-loading tests."""

from __future__ import annotations

from flowguard.plugins import PipelineContext, Plugin


class SamplePlugin(Plugin):
    name = "sample"

    def __init__(self) -> None:
        self.calls: list[str] = []

    def before_pipeline(self, context: PipelineContext) -> None:
        self.calls.append(f"before_pipeline:{context.run_id}")


sample_instance = SamplePlugin()


class BrokenPlugin(Plugin):
    name = "broken"

    def __init__(self) -> None:
        raise RuntimeError("constructor exploded")
