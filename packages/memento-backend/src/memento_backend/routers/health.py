"""Health and readiness."""

from __future__ import annotations

from fastapi import APIRouter

from ..schemas import Health
from .deps import SettingsDep

router = APIRouter(tags=["health"])


@router.get("/health", response_model=Health)
async def health(settings: SettingsDep) -> Health:
    return Health(immich_configured=bool(settings.immich_base_url and settings.immich_api_key))
