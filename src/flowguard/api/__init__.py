"""REST API (FastAPI)."""

from flowguard.api.app import create_app
from flowguard.api.routes import router

__all__ = ["create_app", "router"]
