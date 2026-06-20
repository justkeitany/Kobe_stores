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
import asyncio
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
from app.database import AsyncSessionLocal, get_db
from app.models import Playlist

router = APIRouter(prefix="/api/playlists", tags=["playlists"])
logger = logging.getLogger(__name__)

# Matches  key="value"  attribute pairs inside an #EXTINF line.
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')

# How many sample logos to cache for the card avatar stack.
_LOGO_SAMPLE = 6

# Channel-health probing. We can't test every channel on every refresh (a feed
# may have hundreds), so we probe a spread-out sample and report the ratio. A
# feed URL usually keeps loading long after its channels rot, so this sample is
# what actually tells you a playlist has gone bad.
_PROBE_SAMPLE = 6
_PROBE_TIMEOUT = 10
# How often the background sweep re-checks every saved playlist.
_SWEEP_INTERVAL = 24 * 3600
# Delay the first sweep so it doesn't compete with app startup.
_SWEEP_STARTUP_DELAY = 90


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
        "health": p.health,
        "last_refreshed": p.last_refreshed,
        "last_error": p.last_error,
        "created_at": p.created_at,
    }


def _sample_indices(n: int, k: int) -> list[int]:
    """Evenly spread ``k`` indices across ``range(n)`` (deduped)."""
    if n <= 0:
        return []
    if n <= k:
        return list(range(n))
    return sorted({(i * (n - 1)) // (k - 1) for i in range(k)})


async def _probe_channel(client: httpx.AsyncClient, url: str) -> bool:
    """True if a channel URL resolves to something that looks like a live stream.

    Follows redirects (M3USe 302s to the real upstream) and accepts either a
    video/HLS content-type or a body that starts like an m3u8/segment list.
    Reads only the first chunk so a live .ts stream isn't downloaded.
    """
    try:
        async with client.stream("GET", url.replace(" ", "%20"), timeout=_PROBE_TIMEOUT) as r:
            if r.status_code != 200:
                return False
            ctype = r.headers.get("content-type", "").lower()
            if any(k in ctype for k in ("video", "octet", "mp2t", "mpegts")):
                return True
            async for chunk in r.aiter_bytes():
                head = chunk[:800].decode("utf-8", "replace")
                return ("#EXT" in head) or (".ts" in head) or (".m3u8" in head)
            return False
    except (httpx.HTTPError, UnicodeError):
        return False


async def _assess_channels(channels: list[dict]) -> tuple[int, int]:
    """Probe a spread-out sample of channels; return (alive, sampled)."""
    idxs = _sample_indices(len(channels), _PROBE_SAMPLE)
    if not idxs:
        return (0, 0)
    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        results = await asyncio.gather(*(_probe_channel(client, channels[i]["url"]) for i in idxs))
    return (sum(results), len(results))


async def _refresh_meta(p: Playlist) -> None:
    """Re-fetch the feed, refresh cached card metadata, and assess channel health.

    Network/parse failures are recorded on ``last_error`` rather than raised, so
    a transient upstream hiccup doesn't wipe a playlist or block the UI. The
    feed URL usually keeps loading even when its channels are dead, so we also
    probe a channel sample and surface the ratio on ``health`` — and only raise
    the ``last_error`` flag (the red "Issues?" badge) when the feed is down or
    most sampled channels are offline.
    """
    p.last_refreshed = datetime.now(timezone.utc)
    try:
        channels = _parse_m3u(await _fetch_m3u(p.url))
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("playlist %s refresh failed: %s", p.url, exc)
        p.last_error = "Could not fetch playlist (it may be down or invalid)."
        p.health = "feed offline"
        return

    p.channel_count = len(channels)
    p.logos = _sample_logos(channels)

    if not channels:
        p.health = "no channels"
        p.last_error = "Playlist has no channels"
        return

    alive, sampled = await _assess_channels(channels)
    p.health = f"{alive}/{sampled} live"
    if alive == 0:
        p.last_error = f"All {sampled} sampled channels are offline"
    elif alive <= sampled // 2:
        p.last_error = f"Only {alive} of {sampled} sampled channels are live"
    else:
        p.last_error = None


async def _sweep_all() -> None:
    """Re-check every saved playlist once, committing after each one."""
    async with AsyncSessionLocal() as db:
        playlists = (await db.execute(select(Playlist))).scalars().all()
        for p in playlists:
            await _refresh_meta(p)
            await db.commit()
        if playlists:
            logger.info("Playlist health sweep refreshed %d playlists", len(playlists))


async def playlist_health_loop() -> None:
    """Background task: re-check all playlists daily so dead feeds surface on
    their own without the operator manually hitting Refresh."""
    await asyncio.sleep(_SWEEP_STARTUP_DELAY)
    while True:
        try:
            await _sweep_all()
        except asyncio.CancelledError:
            break
        except Exception as e:  # never let the loop die on a transient error
            logger.error("Playlist health sweep failed: %s", e)
        await asyncio.sleep(_SWEEP_INTERVAL)


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
