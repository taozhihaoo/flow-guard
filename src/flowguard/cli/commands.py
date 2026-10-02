"""FlowGuard command-line interface.

Commands:
    flowguard init       Create a starter ``.flowguard.yml``.
    flowguard validate   Check a workflow file without executing it.
    flowguard run        Execute a workflow locally.
    flowguard history    Browse past runs stored in the local database.
    flowguard show       Show one run in detail.
    flowguard serve      Expose the REST API over HTTP.

All output is rendered with Rich: coloured status, aligned tables, and
human-readable error panels.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from flowguard import __version__
from flowguard.config.parser import (
    DEFAULT_FILENAME,
    find_config_file,
    load_config,
)
from flowguard.core.engine import ExecutionEngine
from flowguard.core.exceptions import ConfigError, FlowGuardError
from flowguard.core.models import PipelineResult, Status, Workflow
from flowguard.core.scheduler import DAG
from flowguard.storage.database import Database, default_db_path

app = typer.Typer(
    name="flowguard",
    help="A local-first workflow automation engine — deterministic pipelines in .flowguard.yml.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

_STATUS_STYLES: dict[Status, str] = {
    Status.SUCCEEDED: "bold green",
    Status.FAILED: "bold red",
    Status.TIMEOUT: "bold yellow",
    Status.SKIPPED: "dim",
    Status.RUNNING: "bold cyan",
    Status.PENDING: "white",
}

_INIT_TEMPLATE = """\
# FlowGuard workflow definition.
# Docs: https://github.com/flowguard/flowguard#configuration
workflow:
  name: my-project

  # Environment variables inherited by every step.
  environment:
    PYTHONUNBUFFERED: "1"

  steps:
    - name: install
      command: [python, -m, pip, install, -e, .]
      timeout: 300

    - name: test
      command: [python, -m, pytest, -q]
      timeout: 600
      depends_on: [install]
      retry:
        attempts: 2
        delay: 2

    - name: lint
      command: [python, -m, ruff, check, .]
      depends_on: [install]

    # Independent of `test`, so lint and test may run in parallel.
    - name: build
      command: [python, -m, build, --wheel]
      depends_on: [test, lint]
      artifacts:
        - dist/*
"""


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"flowguard {__version__}")
        raise typer.Exit


@app.callback()
def _main(
    version: Annotated[
        bool | None,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the FlowGuard version and exit.",
        ),
    ] = None,
) -> None:
    """FlowGuard — a local workflow automation engine."""


def _resolve_config(path: Path | None) -> Path:
    if path is not None:
        return path
    return find_config_file()


def _load_workflow(path: Path | None) -> tuple[Workflow, Path]:
    """Load the workflow, exiting with a readable panel on failure."""
    config_path = _resolve_config(path)
    try:
        return load_config(config_path), config_path
    except ConfigError as exc:
        err_console.print(Panel(str(exc), title="Configuration error", border_style="red"))
        raise typer.Exit(code=1) from exc


def _open_db(db: Path | None) -> Database:
    return Database(db if db is not None else default_db_path())


def _parse_env_overrides(pairs: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            err_console.print(
                Panel(
                    f"Invalid --env value {pair!r}: expected KEY=VALUE.",
                    border_style="red",
                )
            )
            raise typer.Exit(code=2)
        overrides[name] = value
    return overrides


def _select_steps(workflow: Workflow, requested: list[str]) -> Workflow:
    """Restrict the workflow to *requested* steps plus their dependencies."""
    known = set(workflow.step_names)
    missing = sorted(set(requested) - known)
    if missing:
        err_console.print(
            Panel(
                "Unknown step(s): "
                + ", ".join(repr(name) for name in missing)
                + f"\nAvailable steps: {', '.join(workflow.step_names)}",
                border_style="red",
            )
        )
        raise typer.Exit(code=2)
    dag = DAG.from_workflow(workflow)
    keep = set(requested)
    frontier = list(requested)
    while frontier:
        current = frontier.pop()
        for dep in dag.dependencies(current):
            if dep not in keep:
                keep.add(dep)
                frontier.append(dep)
    steps = [step.model_copy() for step in workflow.steps if step.name in keep]
    return workflow.model_copy(update={"steps": steps})


def _format_duration(ms: float) -> str:
    if ms < 1000:
        return f"{ms:.0f}ms"
    seconds = ms / 1000
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, rem = divmod(seconds, 60)
    return f"{int(minutes)}m {rem:04.1f}s"


def _style(status: Status) -> str:
    return _STATUS_STYLES.get(status, "white")


# -- commands ------------------------------------------------------------------


@app.command()
def init(
    path: Annotated[Path, typer.Option(help="Where to write the starter workflow file.")] = Path(
        DEFAULT_FILENAME
    ),
    force: Annotated[bool, typer.Option(help="Overwrite an existing file.")] = False,
) -> None:
    """Create a starter .flowguard.yml to edit for your project."""
    if path.exists() and not force:
        err_console.print(
            Panel(
                f"{path} already exists. Use --force to overwrite it.",
                border_style="yellow",
            )
        )
        raise typer.Exit(code=1)
    path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    console.print(f"[green]Created[/green] {path}")
    console.print(
        "Edit it, then run [bold]flowguard validate[/bold] and [bold]flowguard run[/bold]."
    )


@app.command()
def validate(
    path: Annotated[Path | None, typer.Argument(help="Workflow file to check.")] = None,
) -> None:
    """Validate a workflow file without running it."""
    workflow, config_path = _load_workflow(path)
    table = Table(title=None, show_header=False, box=None, padding=(0, 1))
    table.add_row("[green]✓[/green]", f"{config_path}")
    table.add_row("[green]✓[/green]", f"workflow [bold]{workflow.name}[/bold]")
    table.add_row("[green]✓[/green]", f"{len(workflow.steps)} step(s)")
    for step in workflow.steps:
        deps = f" (depends on {', '.join(step.depends_on)})" if step.depends_on else ""
        table.add_row("", f"  • [bold]{step.name}[/bold]{deps}")
    console.print(Panel(table, title="Workflow is valid", border_style="green", expand=False))


@app.command()
def run(
    path: Annotated[Path | None, typer.Argument(help="Workflow file to execute.")] = None,
    step: Annotated[
        list[str] | None,
        typer.Option("--step", "-s", help="Run only these steps (plus their dependencies)."),
    ] = None,
    env: Annotated[
        list[str] | None,
        typer.Option("--env", "-e", metavar="KEY=VALUE", help="Set an environment variable."),
    ] = None,
    no_parallel: Annotated[
        bool, typer.Option(help="Run steps one at a time, ignoring parallelism.")
    ] = False,
    db: Annotated[Path | None, typer.Option(help="Path to the SQLite history database.")] = None,
    no_artifacts: Annotated[
        bool, typer.Option(help="Disable artifact collection for this run.")
    ] = False,
) -> None:
    """Execute the workflow and store the run in local history."""
    workflow, config_path = _load_workflow(path)
    if step:
        workflow = _select_steps(workflow, list(step))

    database = _open_db(db)
    engine = _build_engine(database, no_parallel=no_parallel, no_artifacts=no_artifacts)
    console.print(
        f"[bold]Running workflow[/bold] [cyan]{workflow.name}[/cyan] "
        f"({len(workflow.steps)} step(s)) from {config_path}"
    )

    try:
        result = asyncio.run(
            engine.run(
                workflow,
                config_dir=config_path.parent,
                env_overrides=_parse_env_overrides(list(env or [])),
            )
        )
    except FlowGuardError as exc:
        err_console.print(Panel(str(exc), title="Execution error", border_style="red"))
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        err_console.print("\n[bold yellow]Interrupted.[/bold yellow]")
        raise typer.Exit(code=130) from None

    _print_run_table(result)
    if result.succeeded:
        console.print(
            f"\n[bold green]✓ Pipeline succeeded[/bold green] "
            f"({_format_duration(result.duration_ms)})"
        )
    else:
        console.print(
            f"\n[bold red]✗ Pipeline failed[/bold red] "
            f"({_format_duration(result.duration_ms)}) — run {result.run_id}"
        )
        raise typer.Exit(code=1)


def _build_engine(database: Database, *, no_parallel: bool, no_artifacts: bool) -> ExecutionEngine:
    return ExecutionEngine(
        storage=database,
        max_workers=1 if no_parallel else None,
        artifact_root=None if no_artifacts else Path(".flowguard") / "artifacts",
    )


def _print_run_table(result: PipelineResult) -> None:
    table = Table(title=f"Run {result.run_id}", header_style="bold")
    table.add_column("Step", style="bold")
    table.add_column("Status")
    table.add_column("Duration", justify="right")
    table.add_column("Attempts", justify="right")
    for step_result in result.step_results:
        table.add_row(
            step_result.step_name,
            f"[{_style(step_result.status)}]{step_result.status.value}[/]",
            _format_duration(step_result.duration_ms),
            str(step_result.attempts) if step_result.attempts else "—",
        )
    console.print(table)


@app.command()
def history(
    db: Annotated[Path | None, typer.Option(help="Path to the SQLite history database.")] = None,
    limit: Annotated[int, typer.Option(min=1, help="Maximum number of runs to list.")] = 15,
    workflow: Annotated[
        str | None, typer.Option(help="Only show runs of this workflow name.")
    ] = None,
    status: Annotated[
        Status | None, typer.Option(case_sensitive=False, help="Only show runs with this status.")
    ] = None,
) -> None:
    """List recent workflow runs recorded in the local database."""
    database = _open_db(db)
    runs = database.list_runs(limit=limit, workflow_name=workflow, status=status)
    if not runs:
        console.print("[dim]No runs recorded yet. Try 'flowguard run'.[/dim]")
        return
    table = Table(title="Run history", header_style="bold")
    table.add_column("Run", style="cyan")
    table.add_column("Workflow", style="bold")
    table.add_column("Status")
    table.add_column("Steps", justify="right")
    table.add_column("Duration", justify="right")
    table.add_column("Started")
    for entry in runs:
        run_status = Status(entry["status"])
        table.add_row(
            entry["run_id"],
            entry["workflow_name"],
            f"[{_style(run_status)}]{run_status.value}[/]",
            str(entry["total_steps"]),
            _format_duration(entry["duration_ms"] or 0.0),
            _format_timestamp(entry["started_at"]),
        )
    console.print(table)


def _format_timestamp(epoch: float) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")


@app.command()
def show(
    run_id: Annotated[str, typer.Argument(help="Run ID from 'flowguard history'.")],
    db: Annotated[Path | None, typer.Option(help="Path to the SQLite history database.")] = None,
) -> None:
    """Show detailed results for a single run."""
    database = _open_db(db)
    record = database.get_run(run_id)
    if record is None:
        err_console.print(Panel(f"No run with ID {run_id!r}.", border_style="red"))
        raise typer.Exit(code=1)
    run_status = Status(record["status"])
    summary = Table(show_header=False, box=None, padding=(0, 1))
    summary.add_row("[bold]Run", record["run_id"])
    summary.add_row("[bold]Workflow", record["workflow_name"])
    summary.add_row("[bold]Status", f"[{_style(run_status)}]{run_status.value}[/]")
    summary.add_row("[bold]Started", _format_timestamp(record["started_at"]))
    summary.add_row(
        "[bold]Duration",
        _format_duration(record["duration_ms"] or 0.0) if record["finished_at"] else "—",
    )
    console.print(Panel(summary, border_style="blue", expand=False))

    steps = database.get_step_results(run_id)
    if steps:
        table = Table(title="Steps", header_style="bold")
        table.add_column("Step", style="bold")
        table.add_column("Status")
        table.add_column("Exit", justify="right")
        table.add_column("Duration", justify="right")
        table.add_column("Attempts", justify="right")
        for entry in steps:
            step_status = Status(entry["status"])
            table.add_row(
                entry["step_name"],
                f"[{_style(step_status)}]{step_status.value}[/]",
                str(entry["exit_code"]) if entry["exit_code"] is not None else "—",
                _format_duration(entry["duration_ms"] or 0.0),
                str(entry["attempts"]) if entry["attempts"] else "—",
            )
        console.print(table)
        failed = [
            entry
            for entry in steps
            if Status(entry["status"]) not in (Status.SUCCEEDED, Status.SKIPPED) and entry["error"]
        ]
        for entry in failed:
            console.print(
                Panel(
                    f"{entry['error']}\n\n[dim]stderr:[/dim] "
                    f"{(entry['stderr'] or '').strip() or '(empty)'}",
                    title=f"Step '{entry['step_name']}'",
                    border_style="red",
                    expand=False,
                )
            )


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = 8642,
    db: Annotated[Path | None, typer.Option(help="Path to the SQLite history database.")] = None,
) -> None:
    """Serve the FlowGuard REST API locally."""
    import uvicorn

    from flowguard.api.app import create_app

    database_path = db if db is not None else default_db_path()
    console.print(
        f"[bold]FlowGuard API[/bold] listening on [cyan]http://{host}:{port}[/cyan] "
        f"(db: {database_path})"
    )
    uvicorn.run(create_app(db_path=database_path), host=host, port=port, log_level="info")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
