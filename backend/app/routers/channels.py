"""
Unified channel directory (/api/channels).

Aggregates EVERY channel the operator has: imported streams plus every channel
from saved playlists (from the cached `Playlist.channels`, so no feeds are
re-fetched). Playlist channels already imported as a stream are deduped out.
The Channels page renders this; status is shown as online / offline / geo only.
"""
import hashlib

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_admin
from app.database import get_db
from app.ffmpeg_manager import ffmpeg_manager
from app.models import AiEvent, Playlist, Stream, StreamCategory
from app.sources import source_urls

router = APIRouter(prefix="/api/channels", tags=["channels"])


class ProbeIn(BaseModel):
    url: str
    name: str | None = None


def _key(prefix: str, val: str) -> str:
    return f"{prefix}_{hashlib.md5(val.encode()).hexdigest()[:10]}"


@router.get("")
async def list_channels(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    cats = {c.id: c.name for c in (await db.execute(select(StreamCategory))).scalars()}
    streams = (await db.execute(select(Stream).options(selectinload(Stream.sources)))).scalars().all()
    statuses = {s["stream_id"]: s for s in await ffmpeg_manager.get_all_statuses()}

    out: list[dict] = []
    imported_urls: set[str] = set()
    for s in streams:
        for u in source_urls(s, s.sources):
            imported_urls.add(u)
        if s.stream_url:
            imported_urls.add(s.stream_url)
        out.append({
            "key": f"s{s.id}",
            "stream_id": s.id,
            "name": s.name,
            "logo": s.logo_url or "",
            "source": cats.get(s.category_id) or "Streams",
            "imported": True,
            "is_enabled": s.is_enabled,
            "status": statuses.get(s.id, {}).get("status", s.status),
        })

    # Playlist channels (cached) not already imported, deduped across playlists.
    seen = set(imported_urls)
    for p in (await db.execute(select(Playlist))).scalars().all():
        for c in (p.channels or []):
            url = c.get("url")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "key": _key("p", url),
                "stream_id": None,
                "name": c.get("name") or "Unnamed",
                "logo": c.get("logo") or "",
                "source": p.name,
                "imported": False,
                "is_enabled": True,
                "status": None,
                "url": url,
            })
    return out


@router.post("/probe")
async def probe_channel(data: ProbeIn, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Check a single (non-imported) channel URL: online / geo / offline.
    Skips while a stream is playing so it can't trip a provider's connection limit."""
    if ffmpeg_manager.active_stream_count() > 0:
        return {"status": "skipped", "note": "A channel is playing — re-check when nothing is streaming."}
    from app.routers.playlists import _probe_status  # reuse the resolver/classifier

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        r = await _probe_status(client, data.url)
    status = {"ready": "online", "geo": "geo", "dead": "offline"}.get(r["status"], "offline")
    name = (data.name or "Channel").strip()
    note = {
        "online": "Source is live and reachable.",
        "geo": "Blocked in this region (HTTP 451).",
        "offline": "Source is down / unreachable.",
    }[status]
    db.add(AiEvent(kind="alert", title=f"{name}: {status}", detail=note,
                   data={"cause": "geo_blocked" if status == "geo" else status, "auto_applied": False}))
    await db.commit()
    return {"status": status, "source": r.get("source"), "note": note}
