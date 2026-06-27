import asyncio
import os
import re
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from pydantic import BaseModel
from sqlalchemy import select, func, update, delete, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.auth import get_current_admin
from app.database import get_db
from app.models import Stream, StreamCategory, StreamSource, BouquetCategory
from app.ffmpeg_manager import ffmpeg_manager, VALID_QUALITIES
from app.youtube import is_youtube_url, clean_youtube_url, proxy_resolve
from app.pluto_stream import resolve as resolve_pluto_url, is_pluto_url
from app.sources import source_refs, source_urls
from app.category_sync import link_category_to_all_bouquets

router = APIRouter(prefix="/api/streams", tags=["streams"])

VALID_DELIVERY_MODES = {"restream", "balanced"}


# ── Schemas ────────────────────────────────────────────────────────────────

class StreamCreate(BaseModel):
    name: str
    stream_url: str
    backup_url: Optional[str] = None
    logo_url: Optional[str] = None
    category_id: Optional[int] = None
    sort_order: int = 0
    epg_channel_id: Optional[str] = None
    delivery_mode: Optional[str] = None
    # auto | low | medium | high. Defaults to "low" for Pluto sources, else "auto".
    quality: Optional[str] = None
    # Ordered source pool. When given it is authoritative; otherwise the pool is
    # derived from stream_url (+ backup_url).
    sources: Optional[list[str]] = None


class StreamUpdate(BaseModel):
    name: Optional[str] = None
    stream_url: Optional[str] = None
    backup_url: Optional[str] = None
    logo_url: Optional[str] = None
    category_id: Optional[int] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    epg_channel_id: Optional[str] = None
    delivery_mode: Optional[str] = None
    quality: Optional[str] = None
    sources: Optional[list[str]] = None


class SourceTest(BaseModel):
    url: str


def _clean_source_list(urls: Optional[list[str]]) -> list[str]:
    """Trim, normalise (YouTube) and de-dupe an incoming source URL list."""
    out: list[str] = []
    for u in urls or []:
        u = _normalize_url((u or "").strip())
        if u and u not in out:
            out.append(u)
    return out


async def _replace_sources(db: AsyncSession, stream: Stream, urls: list[str]) -> None:
    """Rebuild a stream's source pool and keep stream_url/backup_url mirrored."""
    await db.execute(delete(StreamSource).where(StreamSource.stream_id == stream.id))
    for i, url in enumerate(urls):
        db.add(StreamSource(stream_id=stream.id, url=url, priority=i))
    if urls:
        stream.stream_url = urls[0]
        stream.backup_url = urls[1] if len(urls) > 1 else None


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
    q = select(Stream).options(selectinload(Stream.sources))
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
            "delivery_mode": s.delivery_mode,
            "quality": s.quality,
            "source_count": len(source_refs(s, s.sources)),
            "status": statuses.get(s.id, {}).get("status", s.status),
            "viewer_count": statuses.get(s.id, {}).get("viewer_count", 0),
            "active_source_index": statuses.get(s.id, {}).get("active_source_index", 0),
            "last_error": s.last_error,
            "created_at": s.created_at,
        }
        for s in streams
    ]


@router.get("/count")
async def stream_count(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    result = await db.execute(select(func.count(Stream.id)))
    return {"count": result.scalar()}


@router.get("/urls")
async def stream_urls(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Every imported stream's source URL(s), as a flat list.

    Used by the playlist / premium import modal to mark already-imported channels
    as "Added" and stop them being re-imported. The list endpoint is paginated
    (limit 100), so importers must use this complete, lightweight set to dedupe
    rather than `GET /streams`.
    """
    rows = (await db.execute(select(Stream.stream_url, Stream.backup_url))).all()
    urls: set[str] = set()
    for stream_url, backup_url in rows:
        if stream_url:
            urls.add(stream_url)
        if backup_url:
            urls.add(backup_url)
    return {"urls": sorted(urls)}


@router.post("", status_code=201)
async def create_stream(
    data: StreamCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    payload = data.model_dump()
    sources_in = payload.pop("sources", None)
    delivery_mode = payload.pop("delivery_mode", None) or "restream"
    if delivery_mode not in VALID_DELIVERY_MODES:
        raise HTTPException(400, "delivery_mode must be 'restream' or 'balanced'")
    quality = payload.pop("quality", None)
    if quality is not None and quality not in VALID_QUALITIES:
        raise HTTPException(400, f"quality must be one of {sorted(VALID_QUALITIES)}")

    payload["stream_url"] = _normalize_url(payload.get("stream_url"))
    payload["backup_url"] = _normalize_url(payload.get("backup_url"))
    payload["delivery_mode"] = delivery_mode

    # Idempotent import: a channel must never be imported twice. If any incoming
    # URL (primary, backup, or a provided source) already belongs to a stream,
    # return that existing stream instead of creating a duplicate. This is the
    # authoritative guard — the UI also greys out imported channels, but this
    # holds even if a client sends a duplicate anyway.
    candidate_urls = _clean_source_list(
        (sources_in or []) + [payload["stream_url"], payload["backup_url"]]
    )
    if candidate_urls:
        existing = (await db.execute(
            select(Stream).options(selectinload(Stream.sources)).where(
                or_(
                    Stream.stream_url.in_(candidate_urls),
                    Stream.backup_url.in_(candidate_urls),
                    Stream.id.in_(
                        select(StreamSource.stream_id).where(StreamSource.url.in_(candidate_urls))
                    ),
                )
            )
        )).scalars().first()
        if existing:
            return existing

    stream = Stream(**payload)
    db.add(stream)
    await db.flush()  # assign stream.id before adding sources

    pool = _clean_source_list(sources_in) if sources_in is not None else _clean_source_list(
        [stream.stream_url, stream.backup_url]
    )
    await _replace_sources(db, stream, pool)

    # Pluto channels stream more reliably downscaled — default them to low.
    if quality is None:
        quality = "low" if (pool and is_pluto_url(pool[0])) else "auto"
    stream.quality = quality

    await db.commit()
    await db.refresh(stream)
    return stream


@router.get("/{stream_id}")
async def get_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(
        select(Stream).options(selectinload(Stream.sources)).where(Stream.id == stream_id)
    )
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    refs = source_refs(stream, stream.sources)
    return {
        "id": stream.id,
        "name": stream.name,
        "stream_url": stream.stream_url,
        "backup_url": stream.backup_url,
        "logo_url": stream.logo_url,
        "category_id": stream.category_id,
        "is_enabled": stream.is_enabled,
        "sort_order": stream.sort_order,
        "epg_channel_id": stream.epg_channel_id,
        "delivery_mode": stream.delivery_mode,
        "quality": stream.quality,
        "sources": [
            {"url": r.url, "id": r.id, "status": r.status, "priority": r.priority}
            for r in refs
        ],
        "status": stream.status,
        "created_at": stream.created_at,
    }


@router.get("/{stream_id}/sources")
async def list_sources(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(
        select(StreamSource)
        .where(StreamSource.stream_id == stream_id)
        .order_by(StreamSource.priority, StreamSource.id)
    )
    rows = result.scalars().all()
    return [
        {
            "id": r.id,
            "url": r.url,
            "priority": r.priority,
            "is_enabled": r.is_enabled,
            "status": r.status,
            "last_checked": r.last_checked,
            "last_error": r.last_error,
        }
        for r in rows
    ]


@router.post("/sources/test")
async def test_source(
    data: SourceTest,
    _=Depends(get_current_admin),
):
    """Probe a single source URL (used by the source editor before saving)."""
    url = _normalize_url(data.url.strip())
    if is_youtube_url(url):
        resolved = await proxy_resolve(url)
        if not resolved:
            return {"alive": False, "message": "yt-dlp could not resolve the YouTube stream"}
        return await ffmpeg_manager.test_stream_url(resolved)
    return await ffmpeg_manager.test_stream_url(resolve_pluto_url(url))


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
    sources_in = updates.pop("sources", None)
    if "delivery_mode" in updates and updates["delivery_mode"] not in VALID_DELIVERY_MODES:
        raise HTTPException(400, "delivery_mode must be 'restream' or 'balanced'")
    if "quality" in updates and updates["quality"] not in VALID_QUALITIES:
        raise HTTPException(400, f"quality must be one of {sorted(VALID_QUALITIES)}")
    if "stream_url" in updates:
        updates["stream_url"] = _normalize_url(updates["stream_url"])
    if "backup_url" in updates:
        updates["backup_url"] = _normalize_url(updates["backup_url"])
    for k, v in updates.items():
        setattr(stream, k, v)

    if sources_in is not None:
        await _replace_sources(db, stream, _clean_source_list(sources_in))

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
    cat_id = stream.category_id
    # Stop FFmpeg if running
    await ffmpeg_manager.stop_stream(stream_id)
    await db.delete(stream)
    # If this was the last stream in its category, clean up the orphan.
    if cat_id:
        remaining = await db.execute(
            select(func.count()).select_from(Stream).where(Stream.category_id == cat_id)
        )
        if remaining.scalar() == 0:
            await db.execute(
                delete(BouquetCategory).where(BouquetCategory.category_id == cat_id)
            )
            cat = await db.execute(select(StreamCategory).where(StreamCategory.id == cat_id))
            c = cat.scalar_one_or_none()
            if c:
                await db.delete(c)
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

    return await ffmpeg_manager.test_stream_url(resolve_pluto_url(stream.stream_url))


@router.post("/{stream_id}/restart")
async def restart_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(
        select(Stream).options(selectinload(Stream.sources)).where(Stream.id == stream_id)
    )
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    urls = source_urls(stream, stream.sources)
    ok = await ffmpeg_manager.restart_stream(
        stream_id, urls, stream.quality, force_adaptive=stream.force_adaptive
    )
    if not ok:
        # Not running yet, start it
        sp = await ffmpeg_manager.start_stream(
            stream_id, urls, stream.name, quality=stream.quality,
            force_adaptive=stream.force_adaptive,
        )
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
            # Auto-add to every bouquet so imported channels reach users at once.
            await link_category_to_all_bouquets(db, new_cat.id, new_cat.sort_order)
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
            quality="low" if is_pluto_url(stream_url) else "auto",
            status="idle",
        )
        db.add(stream)
        imported += 1

    await db.commit()
    return {"imported": imported, "skipped": skipped, "total": len(channels)}


# ── Stream diagnostics ────────────────────────────────────────────────────

@router.post("/{stream_id}/diagnose")
async def diagnose_stream(
    stream_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Run a full diagnostic on a stream: DNS, HTTP, ffprobe, FFmpeg startup."""
    result = await db.execute(select(Stream).where(Stream.id == stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")

    from app.stream_diag import diagnose
    diag = await diagnose(stream.stream_url, stream.name, stream.id)
    return diag.to_dict()
