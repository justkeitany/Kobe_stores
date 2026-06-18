"""
YouTube stream proxy.

GET /proxy/stream?url=<youtube-url>
    Resolves a fresh HLS manifest for the given YouTube URL (via yt-dlp, cached
    4h) and 302-redirects the player to it. Re-resolves automatically when the
    cached manifest has expired (403/404).

Each distinct `url` is cached independently, so any number of YouTube channels
can be proxied simultaneously without resolving on every request.
"""
import logging

from fastapi import APIRouter, Query, HTTPException
from fastapi.responses import RedirectResponse

from app.youtube import is_youtube_url, proxy_resolve

router = APIRouter(prefix="/proxy", tags=["proxy"])
logger = logging.getLogger(__name__)


@router.get("/stream")
async def proxy_stream(url: str = Query(..., description="YouTube stream URL")):
    if not is_youtube_url(url):
        raise HTTPException(400, "Not a supported YouTube URL")

    resolved = await proxy_resolve(url)
    if not resolved:
        raise HTTPException(502, "Could not resolve YouTube stream")

    # 302 so the player re-requests through the proxy next time (picking up a
    # freshly-resolved manifest once this one expires) rather than caching it.
    return RedirectResponse(resolved, status_code=302)
