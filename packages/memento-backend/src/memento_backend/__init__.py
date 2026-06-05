"""memento-backend — FastAPI service to manage a Memento Smart Frame from an Immich library."""

from .app import create_app

__all__ = ["create_app"]
__version__ = "0.1.1"
