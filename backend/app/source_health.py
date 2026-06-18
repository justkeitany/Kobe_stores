"""
Background health checker for balanced-mode source mirrors.

Restream streams get failover from the FFmpeg manager (it rotates sources on
crash). Balanced streams hand source URLs straight to players, so the panel must
know which mirrors are alive *before* handing one out. This task periodically
probes each enabled source of every balanced stream and records ok/error on the
row; `pick_source_for_user` then steers viewers away from dead mirrors.
"""
import asyncio
import logging
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Stream, StreamSource

logger = logging.getLogger(__name__)

# Probe timeout per source — short so a stalled mirror doesn't hold up the sweep.
_PROBE_TIMEOUT = 6
# Schemes we can probe over HTTP. Anything else (rtmp, udp, rtsp…) can't be
# HEAD-checked, so we leave it "ok" rather than falsely marking it dead.
_HTTP_SCHEMES = ("http", "https")


async def _probe(url: str) -> tuple[str, str | None]:
    """Return (status, error) for one URL: ('ok', None) or ('error', reason)."""
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in _HTTP_SCHEMES:
        return "ok", None
    try:
        async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT, follow_redirects=True) as client:
            resp = await client.head(url)
            # Some origins reject HEAD (405) — confirm with a 1-byte ranged GET.
            if resp.status_code == 405:
                resp = await client.get(url, headers={"Range": "bytes=0-0"})
            if resp.status_code >= 400:
                return "error", f"HTTP {resp.status_code}"
            return "ok", None
    except httpx.HTTPError as e:
        return "error", str(e)[:300]


async def _sweep_once() -> None:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(StreamSource)
            .join(Stream, StreamSource.stream_id == Stream.id)
            .where(
                Stream.delivery_mode == "balanced",
                Stream.is_enabled == True,  # noqa: E712
                StreamSource.is_enabled == True,  # noqa: E712
            )
        )
        sources = result.scalars().all()
        if not sources:
            return

        results = await asyncio.gather(*(_probe(s.url) for s in sources))
        now = datetime.now(timezone.utc)
        for src, (status, err) in zip(sources, results):
            src.status = status
            src.last_error = err
            src.last_checked = now
        await db.commit()


async def health_loop() -> None:
    """Run the sweep forever at HEALTH_CHECK_INTERVAL spacing."""
    interval = max(10, settings.HEALTH_CHECK_INTERVAL)
    while True:
        try:
            await _sweep_once()
        except asyncio.CancelledError:
            break
        except Exception as e:  # never let the loop die on a transient error
            logger.error("Source health sweep failed: %s", e)
        await asyncio.sleep(interval)
