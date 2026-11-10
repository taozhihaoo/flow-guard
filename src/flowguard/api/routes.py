"""REST API route handlers.

The API exposes the local run history and a synchronous execute endpoint:

* ``GET  /health``      — liveness and version.
* ``GET  /runs``        — list recent runs (filterable, paginated).
* ``GET  /runs/{id}``   — one run with its step results.
* ``POST /execute``     — parse and execute a workflow, returning the result.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from flowguard import __version__
from flowguard.api.dashboard import render_dashboard
from flowguard.config.parser import load_config, parse_config
from flowguard.core.engine import ExecutionEngine
from flowguard.core.exceptions import ConfigError, FlowGuardError
from flowguard.core.models import Status
from flowguard.i18n import detect_language, normalize_language
from flowguard.plugins.manager import PluginManager

if TYPE_CHECKING:
    from flowguard.storage.database import Database

__all__ = ["router"]

router = APIRouter()


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(lang: str | None = None) -> HTMLResponse:
    """Serve the built-in, bilingual web dashboard.

    Kept out of the OpenAPI schema: it is a human-facing page, not part of
    the programmatic API contract.
    """
    language = detect_language() if lang is None else normalize_language(lang)
    return HTMLResponse(render_dashboard(language))


class ExecuteRequest(BaseModel):
    """Body of ``POST /execute``.

    Provide either ``path`` (a workflow file on the server's file system)
    or inline ``config`` (the same structure as a ``.flowguard.yml``
    document). ``env`` entries are applied as strongest-priority
    environment overrides for the run.
    """

    path: str | None = Field(default=None, description="Workflow file path.")
    config: dict[str, Any] | None = Field(
        default=None, description="Inline workflow configuration."
    )
    env: dict[str, str] | None = Field(default=None, description="Environment variable overrides.")


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "version": __version__}


@router.get("/runs")
def list_runs(
    request: Request,
    limit: int = 20,
    offset: int = 0,
    workflow: str | None = None,
    status: Status | None = None,
) -> dict[str, Any]:
    """List recent runs, most recent first."""
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    db: Database = request.app.state.db
    runs = db.list_runs(limit=limit + offset, workflow_name=workflow, status=status)
    return {"count": len(runs[offset:]), "runs": runs[offset:]}


@router.get("/runs/{run_id}")
def get_run(run_id: str, request: Request) -> dict[str, Any]:
    """Fetch one run with its step results and event log."""
    db: Database = request.app.state.db
    record = db.get_run(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return {
        "run": record,
        "steps": db.get_step_results(run_id),
        "events": db.get_events(run_id),
    }


@router.post("/execute")
async def execute(body: ExecuteRequest, request: Request) -> dict[str, Any]:
    """Parse and execute a workflow; returns the stored run.

    Execution is synchronous: the response arrives when the pipeline has
    finished, which keeps the endpoint deterministic and simple to script.
    """
    if (body.path is None) == (body.config is None):
        raise HTTPException(
            status_code=422,
            detail="Provide exactly one of 'path' or 'config'.",
        )
    try:
        if body.config is not None:
            workflow = parse_config(body.config, source="<inline config>")
            # No file exists to anchor relative paths; the server's CWD is
            # the only sensible base for an inline workflow.
            config_dir = None
        else:
            assert body.path is not None
            workflow = load_config(body.path)
            # Mirror the CLI: relative working_dir / artifact paths are
            # resolved against the directory holding the workflow file.
            config_dir = Path(body.path).expanduser().resolve().parent
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db: Database = request.app.state.db
    engine = ExecutionEngine(storage=db, plugins=PluginManager())
    try:
        result = await engine.run(
            workflow,
            config_dir=config_dir,
            env_overrides=body.env,
        )
    except FlowGuardError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    record = db.get_run(result.run_id)
    return {
        "run": record,
        "steps": db.get_step_results(result.run_id),
        "status": result.status.value,
    }
