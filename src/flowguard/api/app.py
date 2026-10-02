"""FastAPI application factory for the FlowGuard REST API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

from flowguard import __version__
from flowguard.api.routes import router
from flowguard.storage.database import Database, default_db_path

__all__ = ["create_app"]


def create_app(db_path: str | Path | None = None) -> FastAPI:
    """Create a FlowGuard API application bound to a SQLite database.

    ``db_path`` defaults to ``.flowguard/flowguard.db`` under the current
    directory; ``":memory:"`` keeps everything in RAM (handy for tests).
    The database stays open for the lifetime of the application; the store
    uses one connection per operation, so the API, CLI, and engine can
    share a database file safely.
    """
    application = FastAPI(
        title="FlowGuard API",
        description=(
            "Local workflow automation engine — query run history and "
            "execute workflows defined in .flowguard.yml."
        ),
        version=__version__,
    )
    application.state.db = Database(db_path if db_path is not None else default_db_path())
    application.include_router(router)
    return application
