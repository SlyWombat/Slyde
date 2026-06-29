"""memento-backend — FastAPI service to manage a Memento Smart Frame from an Immich library."""

__version__ = "0.1.42"  # defined before .app import: app/health read it via `from . import`

from .app import create_app

__all__ = ["create_app"]
