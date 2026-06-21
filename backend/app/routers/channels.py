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
from app.models import AiEvent, ChannelHealth, Playlist, Stream, StreamCategory
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
    live = {s["stream_id"]: s for s in await ffmpeg_manager.get_all_statuses()}
    # Real probed health (online | offline | geo) keyed by URL, from the sweep.
    health = {r.url: r.status for r in (await db.execute(select(ChannelHealth))).scalars().all()}

    def resolve(primary_url: str | None, imported: bool, enabled: bool, live_status: str | None) -> str:
        if primary_url and primary_url in health:
            return health[primary_url]          # authoritative: last probe
        if imported and (not enabled or live_status == "error"):
            return "offline"
        return "checking"                        # not probed yet

    out: list[dict] = []
    imported_urls: set[str] = set()
    for s in streams:
        urls = source_urls(s, s.sources) or ([s.stream_url] if s.stream_url else [])
        for u in urls:
            imported_urls.add(u)
        if s.stream_url:
            imported_urls.add(s.stream_url)
        live_status = live.get(s.id, {}).get("status", s.status)
        out.append({
            "key": f"s{s.id}",
            "stream_id": s.id,
            "name": s.name,
            "logo": s.logo_url or "",
            "source": cats.get(s.category_id) or "Streams",
            "imported": True,
            "is_enabled": s.is_enabled,
            "health": resolve(urls[0] if urls else None, True, s.is_enabled, live_status),
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
                "health": resolve(url, False, True, None),
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

    # Persist so the card keeps the real status (and the sweep won't redo it soon).
    from datetime import datetime, timezone
    row = (await db.execute(select(ChannelHealth).where(ChannelHealth.url == data.url))).scalars().first()
    now = datetime.now(timezone.utc)
    if row:
        row.status, row.last_checked = status, now
    else:
        db.add(ChannelHealth(url=data.url, status=status, last_checked=now))
    note = {
        "online": "Source is live and reachable.",
        "geo": "Blocked in this region (HTTP 451).",
        "offline": "Source is down / unreachable.",
    }[status]
    db.add(AiEvent(kind="alert", title=f"{name}: {status}", detail=note,
                   data={"cause": "geo_blocked" if status == "geo" else status, "auto_applied": False}))
    await db.commit()
    return {"status": status, "source": r.get("source"), "note": note}
