"""Pydantic models for the REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: str = "ok"
    immich_configured: bool
    frame_reachable: bool | None = None


class Album(BaseModel):
    id: str
    name: str
    asset_count: int


class Asset(BaseModel):
    id: str
    file_name: str
    type: str


class FrameInfo(BaseModel):
    host: str
    config: dict[str, Any]


class ConfigPatch(BaseModel):
    """Partial frame settings. Only provided fields are changed (merged into the live config)."""

    DisplayOn: bool | None = None
    ShuffleOn: bool | None = None
    NightModeOn: bool | None = None
    PortraitMode: bool | None = None
    DisplayTime: int | None = Field(None, ge=1)
    Name: str | None = None

    def patch(self) -> dict[str, Any]:
        return self.model_dump(exclude_none=True)


class SyncRequest(BaseModel):
    album_id: str | None = None
    asset_ids: list[str] = Field(default_factory=list)


class SyncItem(BaseModel):
    asset_id: str
    dest_name: str
    status: str  # "uploaded" | "skipped" | "failed"
    detail: str | None = None


class SyncResult(BaseModel):
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    items: list[SyncItem] = Field(default_factory=list)


class SyncedPhoto(BaseModel):
    asset_id: str
    dest_name: str
    album_id: str | None = None
    synced_at: str
