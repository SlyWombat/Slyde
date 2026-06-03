"""Pydantic models for the REST API."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: str = "ok"
    immich_configured: bool


class CurrentImage(BaseModel):
    """The image the frame is currently displaying (None when the library is empty)."""

    image: str | None = None


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


class FrameSummary(BaseModel):
    """A discovered frame, for the start/selection screen."""

    name: str
    ip: str
    mac: str = ""
    softver: float = 0.0
    hardver: float = 0.0
    size: int = 0
    orientation: str = ""
    guid: str = ""


class FrameAlbum(BaseModel):
    name: str
    display_name: str
    reserved: bool
    image_count: int
    images: list[str] = Field(default_factory=list)


class CreateAlbumRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)


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
    target_album: str | None = Field(
        default=None, description="Frame album to add the synced images to (created if needed)"
    )


class SyncItem(BaseModel):
    asset_id: str
    dest_name: str
    status: str  # "uploaded" | "skipped" | "failed"
    detail: str | None = None


class SyncResult(BaseModel):
    total: int = 0  # assets considered (for progress)
    uploaded: int = 0
    skipped: int = 0
    failed: int = 0
    removed: int = 0
    items: list[SyncItem] = Field(default_factory=list)


class SyncJobInfo(BaseModel):
    """A background sync job and its live progress (poll until ``status`` != 'running')."""

    id: str
    host: str
    label: str
    status: str  # "running" | "done" | "error"
    error: str | None = None
    result: SyncResult


class SubscribeRequest(BaseModel):
    album_id: str
    target_album: str = Field(min_length=1, max_length=64)


class Subscription(BaseModel):
    immich_album_id: str
    target_album: str
    last_synced_at: str | None = None
    last_result: str | None = None


class SyncedPhoto(BaseModel):
    asset_id: str
    dest_name: str
    album_id: str | None = None
    synced_at: str
