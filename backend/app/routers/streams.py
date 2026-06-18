import asyncio
import os
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy import select, func, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_admin
from app.database import get_db
from app.models import Stream, StreamCategory
from app.ffmpeg_manager import ffmpeg_manager
from app.youtube import is_youtube_url, clean_youtube_url, proxy_resolve

router = APIRouter(prefix="/api/streams", tags=["streams"])


# ── Schemas ────────────────────────────────────────────────────────────────

class StreamCreate(BaseModel):
    name: str
    stream_url: str
    backup_url: Optional[str] = None
    logo_url: Optional[str] = None
    category_id: Optional[int] = None
    sort_order: int = 0
    epg_channel_id: Optional[str] = None


class StreamUpdate(BaseModel):
    name: Optional[str] = None
    stream_url: Optional[str] = None
    backup_url: Optional[str] = None
    logo_url: Optional[str] = None
    category_id: Optional[int] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    epg_channel_id: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────

def _normalize_url(url: Optional[str]) -> Optional[str]:
    """Strip tracking params from YouTube URLs so we store a clean canonical URL."""
    if url and is_youtube_url(url):
        return clean_youtube_url(url)
    return url


def parse_m3u(content: str) -> list[dict]:
    """Parse M3U/M3U8 playlist into list of channel dicts."""
    channels = []
    lines = content.splitlines()
    current = {}

    for line in lines:
        line = line.strip()
        if line.startswith("#EXTINF:"):
            current = {}
            # Extract tvg attributes
            tvg_id = re.search(r'tvg-id="([^"]*)"', line)
            tvg_name = re.search(r'tvg-name="([^"]*)"', line)
            tvg_logo = re.search(r'tvg-logo="([^"]*)"', line)
            group = re.search(r'group-title="([^"]*)"', line)
            # Channel name is after the last comma
            name_part = line.split(",", 1)[-1].strip() if "," in line else ""

            current["name"] = name_part or (tvg_name.group(1) if tvg_name else "Unknown")
            current["tvg_id"] = tvg_id.group(1) if tvg_id else ""
            current["logo"] = tvg_logo.group(1) if tvg_logo else ""
            current["group"] = group.group(1) if group else "Uncategorized"

        elif line and not line.startswith("#") and current:
            current["url"] = line
            channels.append(current)
            current = {}

    return channels


# ── CRUD ────────────────────────────────────────────────────────────────────

@router.get("")
async def list_streams(
    search: Optional[str] = Query(None),
    category_id: Optional[int] = Query(None),
    enabled_only: bool = Query(False),
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    q = select(Stream)
    if search:
        q = q.where(Stream.name.ilike(f"%{search}%"))
    if category_id is not None:
        q = q.where(Stream.category_id == category_id)
    if enabled_only:
        q = q.where(Stream.is_enabled == True)
    q = q.order_by(Stream.sort_order, Stream.id).offset(skip).limit(limit)

    result = await db.execute(q)
    streams = result.scalars().all()

    # Attach live status from FFmpeg manager
    statuses = {s["stream_id"]: s for s in await ffmpeg_manager.get_all_statuses()}

    return [
        {
            "id": s.id,
            "name": s.name,
            "stream_url": s.stream_url,
            "backup_url": s.backup_url,
            "logo_url": s.logo_url,
            "category_id": s.category_id,
            "is_enabled": s.is_enabled,
            "sort_order": s.sort_order,
            "epg_channel_id": s.epg_channel_id,
            "status": statuses.get(s.id, {}).get("status", s.status),
            "viewer_count": statuses.get(s.id, {}).get("viewer_count", 0),
            "last_error": s.last_error,
            "created_at": s.created_at,
        }
        for s in streams
    ]


@router.get("/count")
async def stream_count(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    result = await db.execute(select(func.count(Stream.id)))
    return {"count": result.scalar()}


@router.post("", status_code=201)
async def create_stream(
    data: StreamCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    payload = data.model_dump()
    payload["stream_url"] = _normalize_url(payload.get("stream_url"))
    payload["backup_url"] = _normalize_url(payload.get("backup_url"))
    stream = Stream(**payload)
    db.add(stream)
    await db.commit()
    await db.refresh(stream)
    return stream


@router.get("/{stream_id}")
async def get_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    return stream


@router.put("/{stream_id}")
async def update_stream(
    stream_id: int,
    data: StreamUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    updates = data.model_dump(exclude_none=True)
    if "stream_url" in updates:
        updates["stream_url"] = _normalize_url(updates["stream_url"])
    if "backup_url" in updates:
        updates["backup_url"] = _normalize_url(updates["backup_url"])
    for k, v in updates.items():
        setattr(stream, k, v)
    await db.commit()
    await db.refresh(stream)
    return stream


@router.delete("/{stream_id}", status_code=204)
async def delete_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    # Stop FFmpeg if running
    await ffmpeg_manager.stop_stream(stream_id)
    await db.delete(stream)
    await db.commit()


@router.post("/{stream_id}/test")
async def test_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    # For YouTube streams, the raw URL isn't directly playable — follow the proxy
    # (resolve a fresh manifest via yt-dlp) and test that, never the raw URL.
    if is_youtube_url(stream.stream_url):
        resolved = await proxy_resolve(stream.stream_url)
        if not resolved:
            return {"alive": False, "message": "yt-dlp could not resolve the YouTube stream"}
        return await ffmpeg_manager.test_stream_url(resolved)

    return await ffmpeg_manager.test_stream_url(stream.stream_url)


@router.post("/{stream_id}/restart")
async def restart_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    ok = await ffmpeg_manager.restart_stream(stream_id)
    if not ok:
        # Not running yet, start it
        sp = await ffmpeg_manager.start_stream(stream_id, stream.stream_url, stream.name)
        return {"restarted": True, "status": sp.status}
    return {"restarted": True}


@router.post("/{stream_id}/toggle")
async def toggle_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    stream.is_enabled = not stream.is_enabled
    if not stream.is_enabled:
        await ffmpeg_manager.stop_stream(stream_id)
    await db.commit()
    return {"is_enabled": stream.is_enabled}


# ── M3U Import ──────────────────────────────────────────────────────────────

@router.post("/import/m3u")
async def import_m3u(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Bulk import channels from M3U file. Auto-creates categories from group-title."""
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    channels = parse_m3u(text)

    if not channels:
        raise HTTPException(400, "No valid channels found in M3U file")

    # Build category map
    category_map: dict[str, int] = {}
    existing_cats = await db.execute(select(StreamCategory))
    for cat in existing_cats.scalars():
        category_map[cat.name.lower()] = cat.id

    imported = 0
    skipped = 0

    for ch in channels:
        group = ch.get("group", "Uncategorized")
        group_key = group.lower()

        # Get or create category
        if group_key not in category_map:
            new_cat = StreamCategory(name=group, sort_order=len(category_map))
            db.add(new_cat)
            await db.flush()
            category_map[group_key] = new_cat.id

        cat_id = category_map[group_key]

        stream_url = _normalize_url(ch["url"])

        # Skip duplicate URLs
        dup = await db.execute(select(Stream).where(Stream.stream_url == stream_url))
        if dup.scalar_one_or_none():
            skipped += 1
            continue

        stream = Stream(
            name=ch["name"],
            stream_url=stream_url,
            logo_url=ch.get("logo") or None,
            category_id=cat_id,
            epg_channel_id=ch.get("tvg_id") or None,
            status="idle",
        )
        db.add(stream)
        imported += 1

    await db.commit()
    return {"imported": imported, "skipped": skipped, "total": len(channels)}
