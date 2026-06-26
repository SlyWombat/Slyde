"""Slyde's own asset previews — frame-independent, persisted (issue: Slyde owns previews).

A canonical preview per asset, kept by Slyde regardless of any managed frame (like Immich keeps a
thumbnail). Generated lazily on first request and cached; once cached it serves even when Immich is
unreachable. A frame-specific render ("how it looks on this panel") lives at /frames/{id}/preview.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response

from ..immich import ImmichError
from ..previews import render_canonical_preview
from .deps import AssetPreviewsDep, SettingsDep, get_immich_factory

router = APIRouter(prefix="/assets", tags=["assets"])

ImmichFactory = Annotated[object, Depends(get_immich_factory)]


@router.get("/{asset_id}/preview")
async def asset_preview(
    asset_id: str,
    settings: SettingsDep,
    factory: ImmichFactory,
    previews: AssetPreviewsDep,
) -> Response:
    """Slyde's canonical preview for an asset. Served from Slyde's own store; generated lazily on a
    miss by fetching the asset from Immich once, then kept (survives frame removal / Immich down).
    """
    cached = previews.get(asset_id)
    if cached is not None:
        return Response(content=cached, media_type="image/jpeg")

    if not (settings.immich_base_url and settings.immich_api_key):
        raise HTTPException(status_code=503, detail="Immich is not configured")
    try:
        async with factory() as client:  # type: ignore[operator]
            source = await client.asset_bytes(asset_id, settings.immich_asset_size)
    except ImmichError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    data = await asyncio.to_thread(render_canonical_preview, source)
    previews.put(asset_id, data)
    return Response(content=data, media_type="image/jpeg")
