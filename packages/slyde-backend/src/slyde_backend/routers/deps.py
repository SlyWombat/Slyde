"""Shared FastAPI dependencies that pull services off ``app.state``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from ..config import Settings
from ..firmware import FirmwareService
from ..frames import FrameService
from ..jobs import JobManager
from ..scheduler import SyncScheduler
from ..store import Store
from ..sync import SyncService


def get_settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def get_store(request: Request) -> Store:
    return request.app.state.store  # type: ignore[no-any-return]


def get_frame(request: Request) -> FrameService:
    return request.app.state.frame  # type: ignore[no-any-return]


def get_sync(request: Request) -> SyncService:
    return request.app.state.sync  # type: ignore[no-any-return]


def get_scheduler(request: Request) -> SyncScheduler:
    return request.app.state.scheduler  # type: ignore[no-any-return]


def get_jobs(request: Request) -> JobManager:
    return request.app.state.jobs  # type: ignore[no-any-return]


def get_firmware(request: Request) -> FirmwareService:
    return request.app.state.firmware  # type: ignore[no-any-return]


def get_immich_factory(request: Request):  # type: ignore[no-untyped-def]
    return request.app.state.immich_factory


SettingsDep = Annotated[Settings, Depends(get_settings)]
StoreDep = Annotated[Store, Depends(get_store)]
FrameDep = Annotated[FrameService, Depends(get_frame)]
SyncDep = Annotated[SyncService, Depends(get_sync)]
SchedulerDep = Annotated[SyncScheduler, Depends(get_scheduler)]
JobsDep = Annotated[JobManager, Depends(get_jobs)]
FirmwareDep = Annotated[FirmwareService, Depends(get_firmware)]
