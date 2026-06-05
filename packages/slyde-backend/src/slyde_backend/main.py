"""Uvicorn entry point: ``slyde`` (or ``python -m slyde_backend.main``)."""

from __future__ import annotations

import logging

import uvicorn

from .app import create_app
from .config import get_settings


def main() -> None:
    settings = get_settings()
    # Surface our own logs (sync/scheduler activity, errors) alongside uvicorn's.
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        create_app(settings),
        host=settings.bind_host,
        port=settings.bind_port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
