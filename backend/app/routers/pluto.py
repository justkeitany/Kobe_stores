"""
Pluto TV channel directory passthrough.

GET /api/pluto/channels
    Server-side fetch of https://api.pluto.tv/v2/channels. Proxied through the
    backend because Pluto's API only sends CORS headers for https://pluto.tv,
    so a browser fetch from the panel origin would be blocked.
"""
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_admin

router = APIRouter(prefix="/api/pluto", tags=["pluto"])
logger = logging.getLogger(__name__)

PLUTO_CHANNELS_URL = "https://api.pluto.tv/v2/channels"


@router.get("/channels")
async def list_pluto_channels(_=Depends(get_current_admin)):
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(
                PLUTO_CHANNELS_URL,
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
            resp.raise_for_status()
            return resp.json()
    except httpx.HTTPError as exc:
        logger.warning("Pluto channels fetch failed: %s", exc)
        raise HTTPException(502, "Could not fetch Pluto TV channels")
