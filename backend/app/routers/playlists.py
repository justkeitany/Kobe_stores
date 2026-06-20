"""
Saved M3U playlists.

A playlist is just an external extended-M3U URL the admin wants to keep around
(for example an M3USe shared link). On add/refresh the backend fetches and
parses the feed once to cache lightweight card metadata — the channel count and
a few sample logos — so the Playlists page renders instantly without re-pulling
every multi-MB feed. The full channel list is parsed live on demand via
``GET /{id}/channels``, returned in the same shape the free-streams directory
uses so the frontend can reuse its channel grid + import-to-Streams flow.
"""
import gzip
import logging
import re
from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Playlist

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
logger = logging.getLogger(__name__)

# Matches  key="value"  attribute pairs inside an #EXTINF line.
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')

# How many sample logos to cache for the card avatar stack.
_LOGO_SAMPLE = 6


class PlaylistCreate(BaseModel):
    name: str
    url: str
    description: str | None = None


def _parse_m3u(text: str) -> list[dict]:
    """Parse an extended-M3U playlist into normalized channel dicts.

    Each entry is ``#EXTINF:<dur> <attrs>,<display name>`` followed by the
    stream URL on the next non-comment line. Shape matches the free-streams
    directory the frontend already consumes.
    """
    channels: list[dict] = []
    pending: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(_ATTR_RE.findall(line))
            display = line.split(",", 1)[1].strip() if "," in line else ""
            pending = {
                "id": attrs.get("channel-id") or attrs.get("tvg-id") or "",
                "name": attrs.get("tvg-name") or display or "Unnamed",
                "category": attrs.get("group-title") or "Uncategorized",
                "logo": attrs.get("tvg-logo") or "",
            }
        elif line.startswith("#"):
            continue
        elif pending is not None:
            pending["url"] = line
            if not pending["id"]:
                pending["id"] = line
            channels.append(pending)
            pending = None
    return channels


async def _fetch_m3u(url: str) -> str:
    """Fetch a playlist URL, transparently decompressing gzipped feeds."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
    raw = resp.content
    # Some hosts serve a .gz body without a Content-Encoding header, so httpx
    # won't have decompressed it — detect the gzip magic bytes ourselves.
    if raw[:2] == b"\x1f\x8b":
        try:
            return gzip.decompress(raw).decode("utf-8", "replace")
        except OSError:
            pass
    return resp.text


def _sample_logos(channels: list[dict]) -> list[str]:
    """First few distinct, non-empty channel logos for the card avatar stack."""
    out: list[str] = []
    seen: set[str] = set()
    for c in channels:
        logo = c.get("logo")
        if logo and logo not in seen:
            seen.add(logo)
            out.append(logo)
            if len(out) >= _LOGO_SAMPLE:
                break
    return out


def _serialize(p: Playlist) -> dict:
    return {
        "id": p.id,
        "name": p.name,
        "url": p.url,
        "description": p.description,
        "channel_count": p.channel_count or 0,
        "logos": p.logos or [],
        "last_refreshed": p.last_refreshed,
        "last_error": p.last_error,
        "created_at": p.created_at,
    }


async def _refresh_meta(p: Playlist) -> None:
    """Re-fetch the feed and update cached card metadata in place.

    Network/parse failures are recorded on ``last_error`` rather than raised, so
    a transient upstream hiccup doesn't wipe a playlist or block the UI.
    """
    try:
        channels = _parse_m3u(await _fetch_m3u(p.url))
        p.channel_count = len(channels)
        p.logos = _sample_logos(channels)
        p.last_error = None
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("playlist %s refresh failed: %s", p.url, exc)
        p.last_error = "Could not fetch playlist (it may be down or invalid)."
    p.last_refreshed = datetime.now(timezone.utc)


@router.get("")
async def list_playlists(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Playlist).order_by(Playlist.created_at.desc(), Playlist.id.desc()))
    return [_serialize(p) for p in result.scalars().all()]


@router.post("", status_code=201)
async def create_playlist(
    data: PlaylistCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    name = data.name.strip()
    url = data.url.strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")

    p = Playlist(name=name, url=url, description=(data.description or "").strip() or None)
    await _refresh_meta(p)
    db.add(p)
    await db.commit()
    await db.refresh(p)
    return _serialize(p)


@router.post("/{playlist_id}/refresh")
async def refresh_playlist(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Playlist).where(Playlist.id == playlist_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Playlist not found")
    await _refresh_meta(p)
    await db.commit()
    await db.refresh(p)
    return _serialize(p)


@router.get("/{playlist_id}/channels")
async def list_playlist_channels(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Playlist).where(Playlist.id == playlist_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Playlist not found")
    try:
        channels = _parse_m3u(await _fetch_m3u(p.url))
    except httpx.HTTPError as exc:
        logger.warning("playlist %s channel fetch failed: %s", p.url, exc)
        raise HTTPException(502, "Could not fetch playlist channels")
    return {"playlist_id": p.id, "name": p.name, "channels": channels}


@router.delete("/{playlist_id}", status_code=204)
async def delete_playlist(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Playlist).where(Playlist.id == playlist_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Playlist not found")
    await db.delete(p)
    await db.commit()
