"""Core domain models for FlowGuard.

All models are immutable-by-convention Pydantic models with strict
validation, so an invalid workflow is rejected at load time rather than
mid-execution. These types are shared by the parser, engine, storage
layer, CLI, and REST API.
"""

from __future__ import annotations

import enum
import re
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

#: Step and workflow names must be identifiers usable as file names and in
#: logs. Dots, dashes and underscores are allowed after the first character.
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def validate_name(name: str, kind: str) -> str:
    """Validate a workflow or step *name*, returning it on success.

    Raises ``ValueError`` with a human-readable message otherwise.
    """
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{kind} name must be a non-empty string")
    if not NAME_PATTERN.match(name):
        raise ValueError(
            f"invalid {kind} name {name!r}: names must start with a letter or digit "
            "and may only contain letters, digits, '.', '_' and '-'"
        )
    return name


class Status(enum.StrEnum):
    """Lifecycle status of a step or a whole pipeline run."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"

    @property
    def is_terminal(self) -> bool:
        return self in _TERMINAL_STATUSES

    @property
    def is_success(self) -> bool:
        return self is Status.SUCCEEDED


_TERMINAL_STATUSES = frozenset({Status.SUCCEEDED, Status.FAILED, Status.TIMEOUT, Status.SKIPPED})


class RetryPolicy(BaseModel):
    """Retry configuration for a step.

    Attributes:
        attempts: Total number of attempts (1 means no retry).
        delay: Base delay in seconds before the second attempt.
        backoff_factor: Multiplier applied to the delay after each failed
            attempt (exponential backoff).
        max_delay: Upper bound in seconds for a single retry delay.
    """

    model_config = ConfigDict(extra="forbid")

    attempts: int = Field(default=1, ge=1, le=100)
    delay: float = Field(default=0.0, ge=0, le=3600)
    backoff_factor: float = Field(default=2.0, ge=1.0, le=100)
    max_delay: float | None = Field(default=None, ge=0, le=86400)

    def delay_for(self, failed_attempt: int) -> float:
        """Return the wait in seconds before retry number ``failed_attempt + 1``.

        ``failed_attempt`` is the 1-based number of the attempt that just
        failed. The delay grows exponentially with the backoff factor and is
        capped by ``max_delay`` when set.
        """
        raw = self.delay * (self.backoff_factor ** max(failed_attempt - 1, 0))
        if self.max_delay is not None:
            raw = min(raw, self.max_delay)
        return raw


class Step(BaseModel):
    """A single unit of work inside a workflow.

    Attributes:
        name: Unique identifier of the step within the workflow.
        command: Command to execute — either a list of argument tokens
            (executed directly, no shell) or a string (executed through the
            platform shell).
        timeout: Maximum wall-clock seconds allowed for the command.
        retry: Retry policy applied when the command fails or times out.
        environment: Extra environment variables merged over the parent
            environment for this step only.
        depends_on: Names of steps that must succeed before this one runs.
        artifacts: Glob patterns (relative to the working directory) of
            files or directories to copy into the run's artifact folder.
        working_dir: Directory the command runs in; relative paths are
            resolved against the directory containing the workflow file.
        description: Optional human-readable description.
    """

    model_config = ConfigDict(extra="forbid")

    name: str
    command: str | list[str]
    timeout: float | None = Field(default=None, gt=0, le=86400 * 7)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    environment: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)
    artifacts: list[str] = Field(default_factory=list)
    working_dir: str | None = None
    description: str | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_name(value, "step")

    @field_validator("command")
    @classmethod
    def _check_command(cls, value: str | list[str]) -> str | list[str]:
        if isinstance(value, str):
            if not value.strip():
                raise ValueError("command must not be empty")
            return value
        if isinstance(value, list):
            if not value:
                raise ValueError("command list must contain at least one token")
            for token in value:
                if not isinstance(token, str) or not token.strip():
                    raise ValueError(
                        f"every command token must be a non-empty string, got {token!r}"
                    )
            return value
        raise ValueError("command must be a string or a list of strings")

    @field_validator("environment", mode="before")
    @classmethod
    def _check_environment(cls, value: object) -> object:
        # Runs before core type validation so mistakes produce a readable
        # message instead of a generic pydantic type error.
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ValueError("environment must be a mapping of variable names to values")
        for key, val in value.items():
            if not isinstance(key, str) or not key:
                raise ValueError(
                    f"environment variable names must be non-empty strings, got {key!r}"
                )
            if not isinstance(val, str):
                raise ValueError(
                    f"environment variable {key!r} must map to a string, got {type(val).__name__}"
                )
        return value

    @field_validator("depends_on", "artifacts")
    @classmethod
    def _check_string_list(cls, value: list[str]) -> list[str]:
        seen: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(f"list entries must be non-empty strings, got {item!r}")
            if item not in seen:
                seen.append(item)
        return seen

    @field_validator("working_dir")
    @classmethod
    def _check_working_dir(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("working_dir must not be empty")
        return value

    def command_tokens(self) -> str | list[str]:
        """Return the command in the form used for subprocess dispatch."""
        return self.command


class Workflow(BaseModel):
    """A named, ordered collection of steps forming a pipeline."""

    model_config = ConfigDict(extra="forbid")

    name: str
    version: str | None = None
    environment: dict[str, str] = Field(default_factory=dict)
    steps: list[Step] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        return validate_name(value, "workflow")

    @model_validator(mode="after")
    def _check_step_structure(self) -> Self:
        """Enforce cross-step invariants on any directly constructed Workflow.

        (The YAML parser reports these conditions itself so it can merge
        them into a combined human-readable report.)
        """
        names = [step.name for step in self.steps]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError("duplicate step name(s): " + ", ".join(repr(n) for n in duplicates))

        known = set(names)
        for step in self.steps:
            unknown = [dep for dep in step.depends_on if dep not in known]
            if unknown:
                raise ValueError(
                    f"step {step.name!r} depends on unknown step(s): "
                    + ", ".join(repr(dep) for dep in unknown)
                )
        return self

    def get_step(self, name: str) -> Step:
        """Return the step called ``name``; raises ``KeyError`` if absent."""
        for step in self.steps:
            if step.name == name:
                return step
        raise KeyError(name)

    @property
    def step_names(self) -> list[str]:
        return [step.name for step in self.steps]


class StepResult(BaseModel):
    """Outcome of executing (or skipping) a single step."""

    model_config = ConfigDict(extra="forbid")

    step_name: str
    status: Status = Status.PENDING
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    error: str | None = None
    duration_ms: float = 0.0
    attempts: int = 0
    started_at: float = 0.0
    finished_at: float = 0.0
    artifacts: list[str] = Field(default_factory=list)

    @property
    def succeeded(self) -> bool:
        return self.status is Status.SUCCEEDED


class PipelineResult(BaseModel):
    """Outcome of a complete workflow run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    workflow_name: str
    status: Status = Status.PENDING
    step_results: list[StepResult] = Field(default_factory=list)
    started_at: float = 0.0
    finished_at: float = 0.0
    duration_ms: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.status is Status.SUCCEEDED

    def step(self, name: str) -> StepResult | None:
        """Return the result for step ``name``, or ``None`` if not present."""
        for result in self.step_results:
            if result.step_name == name:
                return result
        return None


#: Documented name for the outcome of executing a single step.
ExecutionResult = StepResult
