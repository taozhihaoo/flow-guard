"""FlowGuard command-line interface.

Commands:
    flowguard init       Create a starter ``.flowguard.yml``.
    flowguard validate   Check a workflow file without executing it.
    flowguard run        Execute a workflow locally.
    flowguard history    Browse past runs stored in the local database.
    flowguard show       Show one run in detail.
    flowguard serve      Expose the REST API and web dashboard over HTTP.

All output is rendered with Rich through a shared visual system
(:mod:`flowguard.cli.presentation`): a gradient wordmark, rounded panels,
colour-coded status pills, dependency-graph previews, and compact data
tables. ``--lang`` / ``FLOWGUARD_LANG`` switches the whole UI between
English (default) and Chinese.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from flowguard import __version__
from flowguard.cli import presentation as ui
from flowguard.config.parser import (
    DEFAULT_FILENAME,
    find_config_file,
    load_config,
)
from flowguard.core.engine import ExecutionEngine
from flowguard.core.exceptions import ConfigError, FlowGuardError
from flowguard.core.models import Status, Workflow
from flowguard.core.scheduler import DAG
from flowguard.i18n import Language, detect_language, messages, normalize_language
from flowguard.storage.database import Database, default_db_path

app = typer.Typer(
    name="flowguard",
    help="A local-first workflow automation engine — deterministic pipelines in .flowguard.yml.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

#: Active UI language. Set by the top-level callback before any command runs.
_LANG: Language = Language.EN

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


def _s(key: str) -> str:
    """Translate a UI key using the active language."""
    return messages(_LANG)[key]


def _set_language(value: str | None) -> None:
    global _LANG
    _LANG = normalize_language(value) if value else detect_language()


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
    lang: Annotated[
        str | None,
        typer.Option(
            "--lang",
            envvar="FLOWGUARD_LANG",
            help="UI language: en (default) or zh. Also reads FLOWGUARD_LANG.",
        ),
    ] = None,
) -> None:
    """FlowGuard — a local workflow automation engine."""
    _set_language(lang)


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
        err_console.print(ui.error_panel(str(exc), lang=_LANG, title=_s("cli.config_error")))
        raise typer.Exit(code=1) from exc


def _open_db(db: Path | None) -> Database:
    return Database(db if db is not None else default_db_path())


def _parse_env_overrides(pairs: list[str]) -> dict[str, str]:
    overrides: dict[str, str] = {}
    for pair in pairs:
        name, separator, value = pair.partition("=")
        if not separator or not name:
            err_console.print(
                ui.error_panel(
                    f"Invalid --env value {pair!r}: {_s('cli.env_invalid')} (KEY=VALUE).",
                    lang=_LANG,
                    title="FlowGuard",
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
            ui.error_panel(
                f"{_s('cli.unknown_steps')}: "
                + ", ".join(repr(name) for name in missing)
                + f"\n{_s('cli.available_steps')}: {', '.join(workflow.step_names)}",
                lang=_LANG,
                title="FlowGuard",
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
            ui.error_panel(
                f"{path} {_s('cli.init.exists')}",
                lang=_LANG,
                title="FlowGuard",
                hint="flowguard init --force",
            )
        )
        raise typer.Exit(code=1)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_INIT_TEMPLATE, encoding="utf-8")
    except OSError as exc:
        err_console.print(
            ui.error_panel(
                f"{_s('cli.init.write_failed')} {path}: {exc}",
                lang=_LANG,
                title="FlowGuard",
            )
        )
        raise typer.Exit(code=1) from exc
    console.print(ui.brand_header(_LANG))
    console.print(ui.init_panel(str(path), _LANG))


@app.command()
def validate(
    path: Annotated[Path | None, typer.Argument(help="Workflow file to check.")] = None,
) -> None:
    """Validate a workflow file without running it."""
    workflow, config_path = _load_workflow(path)
    dag = DAG.from_workflow(workflow)
    dag.topological_order()
    console.print(ui.brand_header(_LANG))
    console.print(ui.validate_panel(workflow, str(config_path), dag, _LANG))


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
    levels = DAG.from_workflow(workflow).levels()

    console.print(ui.brand_header(_LANG))
    console.print(
        ui.run_header(
            workflow.name,
            str(config_path),
            step_count=len(workflow.steps),
            level_count=len(levels),
            parallel=not no_parallel,
            lang=_LANG,
        )
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
        err_console.print(ui.error_panel(str(exc), lang=_LANG, title=_s("cli.execution_error")))
        raise typer.Exit(code=1) from exc
    except KeyboardInterrupt:
        err_console.print(Text(f"\n  !  {_s('cli.interrupted')}", style="bold #fbbf24"))
        raise typer.Exit(code=130) from None

    console.print(ui.results_table(result, _LANG))
    for panel in ui.failure_detail_panels(result, _LANG):
        console.print(panel)
    console.print(ui.summary_panel(result, _LANG))
    if not result.succeeded:
        raise typer.Exit(code=1)


def _build_engine(database: Database, *, no_parallel: bool, no_artifacts: bool) -> ExecutionEngine:
    return ExecutionEngine(
        storage=database,
        max_workers=1 if no_parallel else None,
        artifact_root=Path(".flowguard") / "artifacts",
        collect_artifacts=not no_artifacts,
    )


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
    console.print(ui.brand_header(_LANG))
    if not runs:
        console.print(_empty_history_panel())
        return
    console.print(ui.history_table(runs, lang=_LANG, total=database.count_runs()))


def _empty_history_panel() -> Panel:
    """Quiet, non-alarming empty state for ``flowguard history``."""
    return Panel(
        Text(f"  ○  {_s('cli.run.no_runs')}", style="#7c93b8"),
        box=box.ROUNDED,
        border_style="#2b3a55",
        padding=(0, 1),
    )


@app.command()
def show(
    run_id: Annotated[str, typer.Argument(help="Run ID from 'flowguard history'.")],
    db: Annotated[Path | None, typer.Option(help="Path to the SQLite history database.")] = None,
) -> None:
    """Show detailed results for a single run."""
    database = _open_db(db)
    record = database.get_run(run_id)
    console.print(ui.brand_header(_LANG))
    if record is None:
        err_console.print(
            ui.error_panel(
                f"{_s('cli.show.not_found')} {run_id!r}.",
                lang=_LANG,
                title="FlowGuard",
                hint="flowguard history",
            )
        )
        raise typer.Exit(code=1)

    console.print(ui.show_header(record, lang=_LANG))
    steps = database.get_step_results(run_id)
    if steps:
        console.print(ui.show_steps_table(steps, lang=_LANG))
        for entry in steps:
            step_status = entry["status"]
            if step_status not in ("succeeded", "skipped") and entry["error"]:
                console.print(ui.show_failure_panel(entry, lang=_LANG))


@app.command()
def serve(
    host: Annotated[str, typer.Option(help="Bind address.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(min=1, max=65535, help="Port to listen on.")] = 8642,
    db: Annotated[Path | None, typer.Option(help="Path to the SQLite history database.")] = None,
) -> None:
    """Serve the FlowGuard REST API and web dashboard locally."""
    import uvicorn

    from flowguard.api.app import create_app

    database_path = db if db is not None else default_db_path()
    loopback = host in {"127.0.0.1", "localhost", "::1"}
    console.print(ui.brand_header(_LANG))
    console.print(
        ui.serve_panel(
            host,
            port,
            str(database_path),
            lang=_LANG,
            insecure=not loopback,
        )
    )
    uvicorn.run(create_app(db_path=database_path), host=host, port=port, log_level="info")


def main() -> None:
    """Console-script entry point."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
