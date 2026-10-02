"""Loading and validation of ``.flowguard.yml`` workflow files.

The parser turns raw YAML into :class:`~flowguard.core.models.Workflow`
models and converts every problem it can detect into a human-readable,
multi-line error report: missing fields, wrong types, duplicate step names,
unknown dependencies, and dependency cycles are all reported with the name
of the offending step or field — never as a raw Python traceback.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from flowguard.core.exceptions import (
    ConfigError,
    CycleError,
    UnknownDependencyError,
)
from flowguard.core.models import RetryPolicy, Workflow
from flowguard.core.scheduler import DAG

__all__ = [
    "DEFAULT_FILENAME",
    "find_config_file",
    "load_config",
    "parse_config",
]

#: Primary configuration file name, discovered by ``flowguard init/run``.
DEFAULT_FILENAME = ".flowguard.yml"

#: Alternative file name accepted by :func:`find_config_file`.
ALTERNATE_FILENAMES = (".flowguard.yml", ".flowguard.yaml")

_FRIENDLY_TYPE_NAMES = {
    "str": "text",
    "int": "integer",
    "float": "number",
    "bool": "boolean",
}

_TYPE_EXPECTATIONS = {
    "steps": "a list of steps",
    "command": "a string or a list of strings",
    "retry": "a mapping with keys like 'attempts' and 'delay'",
    "environment": "a mapping of variable names to values",
    "depends_on": "a list of step names",
    "artifacts": "a list of glob patterns",
    "timeout": "a positive number of seconds",
}


def _format_location(loc: tuple[Any, ...]) -> str:
    parts: list[str] = []
    for part in loc:
        if isinstance(part, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{part}]"
            else:
                parts.append(f"[{part}]")
        else:
            parts.append(str(part))
    return ".".join(parts) if parts else "configuration"


def _pydantic_error_lines(exc: ValidationError) -> list[str]:
    """Render a Pydantic ``ValidationError`` as a list of report lines.

    ``value_error`` entries are skipped: those come from the Workflow
    model's own cross-step checks, which :func:`_structural_issues`
    reports in a cleaner form.
    """
    lines: list[str] = []
    for error in exc.errors():
        location = _format_location(error.get("loc", ()))
        error_type = error.get("type", "")
        if error_type == "value_error":
            continue
        if error_type == "missing":
            lines.append(f"'{location}' is required but missing")
            continue
        message = error.get("msg", "invalid value")
        if error_type == "extra_forbidden":
            lines.append(
                f"unknown field '{location}' — check for typos (input: {error.get('input')!r})"
            )
            continue
        expectation = _TYPE_EXPECTATIONS.get(str(location).split(".")[-1])
        if expectation and error_type.startswith(("string_type", "list_type", "dict_type")):
            lines.append(f"'{location}': expected {expectation}, got {message}")
        else:
            lines.append(f"'{location}': {message}")
    return lines


def _structural_issues(section: dict[str, Any]) -> list[str]:
    """Cross-step problems detectable from raw config (before model build)."""
    issues: list[str] = []
    steps = section.get("steps")
    if not isinstance(steps, list):
        return issues

    names = [
        step["name"]
        for step in steps
        if isinstance(step, dict) and isinstance(step.get("name"), str)
    ]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        issues.append("duplicate step name(s): " + ", ".join(repr(name) for name in duplicates))

    known = set(names)
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        name = step.get("name")
        label = f"step {name!r}" if isinstance(name, str) else f"step #{position}"
        depends_on = step.get("depends_on")
        if isinstance(depends_on, list):
            unknown = [dep for dep in depends_on if isinstance(dep, str) and dep not in known]
            if unknown:
                issues.append(
                    f"{label} depends on unknown step(s): "
                    + ", ".join(repr(dep) for dep in unknown)
                )
    return issues


def parse_config(data: Any, source: str = "<input>") -> Workflow:
    """Validate raw configuration *data* and return a :class:`Workflow`.

    ``data`` must look like::

        workflow:
          name: my-workflow
          steps:
            - name: build
              command: ["make"]

    Raises :class:`ConfigError` with a human-readable message describing
    every problem found (missing fields, invalid commands, duplicate step
    names, unknown ``depends_on`` references, dependency cycles) — all
    problems found are listed together, not one at a time.
    """
    if not isinstance(data, dict):
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - top level must be a mapping with a 'workflow' key, "
            f"got {_type_name(data)}"
        )

    if "workflow" not in data:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            "  - missing required section 'workflow'\n"
            "  Expected structure:\n"
            "    workflow:\n"
            "      name: my-workflow\n"
            "      steps:\n"
            "        - name: build\n"
            '          command: ["make"]'
        )

    workflow_section = data["workflow"]
    if not isinstance(workflow_section, dict):
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - 'workflow' must be a mapping, got {_type_name(workflow_section)}"
        )

    _pre_validate_section(workflow_section, source)

    # Collect every detectable problem and report them together in one
    # message: field-level errors plus cross-step structural issues.
    problems: list[str] = []
    workflow: Workflow | None = None
    try:
        workflow = Workflow.model_validate(workflow_section)
    except ValidationError as exc:
        problems.extend(_pydantic_error_lines(exc))  # value_error lines skipped
    problems.extend(_structural_issues(workflow_section))

    if problems or workflow is None:
        if not problems:
            problems = ["configuration could not be validated (internal error)"]
        body = "\n".join(f"  - {problem}" for problem in problems)
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n{body}\n"
            "See https://github.com/flowguard/flowguard#configuration for the schema."
        )

    # Graph checks beyond the model: cycles cannot be expressed as a field
    # constraint, so detect them here with a readable message.
    try:
        DAG.from_workflow(workflow).topological_order()
    except (CycleError, UnknownDependencyError, ValueError) as exc:
        raise ConfigError(f"Invalid workflow configuration in {source}:\n  - {exc}") from exc
    return workflow


def _type_name(value: Any) -> str:
    if value is None:
        return "nothing (empty document)"
    return f"{type(value).__name__}"


def _pre_validate_section(section: dict[str, Any], source: str) -> None:
    """Catch the most common structural mistakes with tailored messages.

    Pydantic would reject these too, but the messages below name the exact
    step and expectation, which is what users actually need.
    """
    name = section.get("name")
    if name is None:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            "  - 'workflow.name' is required but missing"
        )
    if not isinstance(name, str):
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - 'workflow.name' must be text, got {name!r}"
        )

    steps = section.get("steps")
    if steps is None:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            "  - 'workflow.steps' is required but missing — a workflow needs "
            "at least one step"
        )
    if not isinstance(steps, list):
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - 'workflow.steps' must be a list of steps, got {_type_name(steps)}"
        )
    if not steps:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            "  - 'workflow.steps' is empty — a workflow needs at least one step"
        )

    for position, raw_step in enumerate(steps, start=1):
        _pre_validate_step(raw_step, position, source)


def _pre_validate_step(raw_step: Any, position: int, source: str) -> None:
    label = f"step #{position}"
    if isinstance(raw_step, dict) and isinstance(raw_step.get("name"), str):
        label = f"step '{raw_step['name']}' (#{position})"

    if not isinstance(raw_step, dict):
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - {label} must be a mapping with at least 'name' and 'command', "
            f"got {_type_name(raw_step)}"
        )

    if raw_step.get("name") is None:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - {label}: 'name' is required but missing"
        )

    if raw_step.get("command") is None:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - {label}: 'command' is required but missing — every step needs "
            "a command to execute"
        )

    command = raw_step["command"]
    if isinstance(command, list) and not command:
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - {label}: 'command' is an empty list — provide at least one token"
        )
    if isinstance(command, list):
        for token in command:
            if not isinstance(token, str):
                raise ConfigError(
                    f"Invalid workflow configuration in {source}:\n"
                    f"  - {label}: every command token must be a string, "
                    f"got {token!r}"
                )
    elif not isinstance(command, str):
        raise ConfigError(
            f"Invalid workflow configuration in {source}:\n"
            f"  - {label}: 'command' must be a string or a list of strings, "
            f"got {command!r}"
        )

    retry = raw_step.get("retry")
    if retry is not None and isinstance(retry, dict):
        unknown = set(retry) - set(RetryPolicy.model_fields)
        if unknown:
            raise ConfigError(
                f"Invalid workflow configuration in {source}:\n"
                f"  - {label}: unknown retry option(s) "
                f"{', '.join(sorted(repr(k) for k in unknown))} — "
                "valid options are: attempts, delay, backoff_factor, max_delay"
            )


def load_config(path: str | Path) -> Workflow:
    """Load, parse, and validate the workflow file at ``path``.

    Raises :class:`ConfigError` if the file is missing, unreadable, not
    valid YAML, or fails schema validation.
    """
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Configuration file not found: {path}")
    if path.is_dir():
        raise ConfigError(
            f"Configuration path is a directory, not a file: {path} "
            f"(expected a file such as {DEFAULT_FILENAME})"
        )
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read configuration file {path}: {exc}") from exc
    return parse_yaml_text(raw, source=str(path))


def parse_yaml_text(raw: str, source: str = "<input>") -> Workflow:
    """Parse YAML *text* into a validated :class:`Workflow`."""
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        detail = str(exc).replace("\n", "\n      ")
        raise ConfigError(f"Invalid YAML syntax in {source}:\n      {detail}") from exc
    return parse_config(data, source=source)


def find_config_file(directory: str | Path | None = None) -> Path:
    """Locate ``.flowguard.yml`` (or ``.flowguard.yaml``) in *directory*.

    Defaults to the current working directory. Raises :class:`ConfigError`
    when neither file exists.
    """
    directory = Path(directory) if directory is not None else Path.cwd()
    for candidate in ALTERNATE_FILENAMES:
        path = directory / candidate
        if path.is_file():
            return path
    searched = " or ".join(ALTERNATE_FILENAMES)
    raise ConfigError(
        f"No workflow configuration found in {directory} — "
        f"expected a {searched} file. Run 'flowguard init' to create one."
    )
