"""Unit tests for core domain models (Phase 1)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from flowguard.core.models import (
    PipelineResult,
    RetryPolicy,
    Status,
    Step,
    StepResult,
    Workflow,
)


class TestStep:
    def test_minimal_step_defaults(self):
        step = Step(name="build", command=["make"])
        assert step.name == "build"
        assert step.timeout is None
        assert step.retry.attempts == 1
        assert step.environment == {}
        assert step.depends_on == []
        assert step.artifacts == []
        assert step.working_dir is None

    def test_full_step_round_trip(self):
        step = Step(
            name="test",
            command=["pytest", "-q"],
            timeout=60,
            retry=RetryPolicy(attempts=3, delay=2),
            environment={"CI": "local"},
            depends_on=["install"],
            artifacts=["reports/*"],
            working_dir="pkg",
        )
        data = step.model_dump()
        assert Step.model_validate(data) == step

    def test_command_string_accepted(self):
        step = Step(name="build", command="make all")
        assert isinstance(step.command, str)

    def test_command_string_shell_hint_documented(self):
        # String commands run through the platform shell; list commands do not.
        assert Step(name="a", command="echo hi").command_tokens() == "echo hi"
        assert Step(name="b", command=["echo", "hi"]).command_tokens() == ["echo", "hi"]

    @pytest.mark.parametrize("command", ["", "   "])
    def test_empty_string_command_rejected(self, command: str):
        with pytest.raises(ValidationError, match="empty"):
            Step(name="a", command=command)

    def test_empty_list_command_rejected(self):
        with pytest.raises(ValidationError, match="at least one token"):
            Step(name="a", command=[])

    def test_non_string_command_token_rejected(self):
        with pytest.raises(ValidationError):
            Step(name="a", command=["echo", 42])

    def test_invalid_step_name_rejected(self):
        with pytest.raises(ValidationError, match="invalid step name"):
            Step(name="bad name", command=["x"])

    def test_step_name_must_not_be_empty(self):
        with pytest.raises(ValidationError):
            Step(name="  ", command=["x"])

    def test_step_name_accepts_common_characters(self):
        for name in ("build", "build_wheel", "build-wheel", "build.wheel", "2fast"):
            assert Step(name=name, command=["x"]).name == name

    def test_timeout_must_be_positive(self):
        with pytest.raises(ValidationError):
            Step(name="a", command=["x"], timeout=0)

    def test_timeout_negative_rejected(self):
        with pytest.raises(ValidationError):
            Step(name="a", command=["x"], timeout=-5)

    def test_environment_values_must_be_strings(self):
        with pytest.raises(ValidationError, match="must map to a string"):
            Step(name="a", command=["x"], environment={"N": 3})

    def test_depends_on_deduplicated_preserving_order(self):
        step = Step(name="a", command=["x"], depends_on=["b", "c", "b"])
        assert step.depends_on == ["b", "c"]

    def test_unknown_extra_field_rejected(self):
        with pytest.raises(ValidationError, match=r"extra_forbidden|extra"):
            Step(name="a", command=["x"], commands=["x"])


class TestRetryPolicy:
    def test_defaults(self):
        policy = RetryPolicy()
        assert policy.attempts == 1
        assert policy.delay == 0.0
        assert policy.backoff_factor == 2.0
        assert policy.max_delay is None

    def test_attempts_must_be_at_least_one(self):
        with pytest.raises(ValidationError):
            RetryPolicy(attempts=0)

    def test_negative_delay_rejected(self):
        with pytest.raises(ValidationError):
            RetryPolicy(delay=-1)

    def test_backoff_factor_below_one_rejected(self):
        with pytest.raises(ValidationError):
            RetryPolicy(backoff_factor=0.5)

    def test_exponential_delays(self):
        policy = RetryPolicy(delay=2, backoff_factor=2.0)
        assert policy.delay_for(1) == 2  # before 2nd attempt
        assert policy.delay_for(2) == 4  # before 3rd attempt
        assert policy.delay_for(3) == 8  # before 4th attempt

    def test_first_delay_equals_base_delay(self):
        assert RetryPolicy(delay=3).delay_for(1) == 3

    def test_max_delay_caps_backoff(self):
        policy = RetryPolicy(delay=2, backoff_factor=10, max_delay=5)
        assert policy.delay_for(1) == 2
        assert policy.delay_for(2) == 5
        assert policy.delay_for(3) == 5

    def test_zero_delay_stays_zero(self):
        policy = RetryPolicy(delay=0, backoff_factor=3)
        assert policy.delay_for(5) == 0


class TestWorkflow:
    def _step(self, name: str, **extra: object) -> dict[str, object]:
        return {"name": name, "command": ["echo", name], **extra}

    def test_valid_workflow(self):
        workflow = Workflow(name="demo", steps=[Step(name="a", command=["x"])])
        assert workflow.step_names == ["a"]

    def test_duplicate_step_names_rejected(self):
        with pytest.raises(ValidationError, match="duplicate step name"):
            Workflow(
                name="demo",
                steps=[Step(name="a", command=["x"]), Step(name="a", command=["y"])],
            )

    def test_unknown_dependency_rejected(self):
        with pytest.raises(ValidationError, match="unknown step"):
            Workflow(
                name="demo",
                steps=[Step(name="a", command=["x"], depends_on=["ghost"])],
            )

    def test_empty_steps_rejected(self):
        with pytest.raises(ValidationError):
            Workflow(name="demo", steps=[])

    def test_get_step_found_and_missing(self):
        workflow = Workflow(name="demo", steps=[Step(name="a", command=["x"])])
        assert workflow.get_step("a").name == "a"
        with pytest.raises(KeyError):
            workflow.get_step("nope")

    def test_workflow_environment_default(self):
        workflow = Workflow(name="demo", steps=[Step(name="a", command=["x"])])
        assert workflow.environment == {}

    def test_workflow_name_validation(self):
        with pytest.raises(ValidationError, match="invalid workflow name"):
            Workflow(name="not valid!", steps=[Step(name="a", command=["x"])])


class TestStatuses:
    def test_terminal_statuses(self):
        assert Status.SUCCEEDED.is_terminal
        assert Status.FAILED.is_terminal
        assert Status.TIMEOUT.is_terminal
        assert Status.SKIPPED.is_terminal
        assert not Status.RUNNING.is_terminal
        assert not Status.PENDING.is_terminal

    def test_success_flag(self):
        assert Status.SUCCEEDED.is_success
        assert not Status.FAILED.is_success

    def test_status_values_are_strings(self):
        assert Status.SUCCEEDED.value == "succeeded"
        assert Status("failed") is Status.FAILED


class TestResults:
    def test_step_result_defaults(self):
        result = StepResult(step_name="a", status=Status.FAILED)
        assert result.exit_code is None
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.error is None
        assert not result.succeeded

    def test_step_result_success_property(self):
        assert StepResult(step_name="a", status=Status.SUCCEEDED).succeeded

    def test_pipeline_result_step_lookup(self):
        result = PipelineResult(
            run_id="r1",
            workflow_name="wf",
            step_results=[StepResult(step_name="a", status=Status.SUCCEEDED)],
        )
        assert result.step("a") is not None
        assert result.step("missing") is None

    def test_execution_result_alias(self):
        # The documented API name for a step's outcome.
        from flowguard.core.models import ExecutionResult

        assert ExecutionResult is StepResult

    def test_pipeline_result_success(self):
        assert not PipelineResult(run_id="r", workflow_name="w", status=Status.FAILED).succeeded
