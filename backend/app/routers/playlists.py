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
from fastapi.responses import Response
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import AsyncSessionLocal, get_db
from app.ffmpeg_manager import ffmpeg_manager
from app.models import Playlist, Stream, StreamCategory, BouquetCategory

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
# Max concurrent channel probes. Kept low because each probe opens a connection
# to the same upstream the player uses; connection-limited accounts (M3USe
# trial) reject parallel connections with "multiple connections detected".
_PROBE_CONCURRENCY = 2
# How often the background sweep re-checks every saved playlist.
_SWEEP_INTERVAL = 24 * 3600
# Delay the first sweep so it doesn't compete with app startup.
_SWEEP_STARTUP_DELAY = 90
# When the sweep is skipped because a stream is playing, retry this soon.
_SWEEP_SKIP_RETRY = 5 * 60


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


def _source_from(final_url: str, body: str = "") -> str:
    """Best-effort source label for a channel.

    M3USe often resolves a channel through a nested playlist (e.g. a GitHub
    ``YouTube_to_m3u`` asset) before the real CDN, so the final host alone isn't
    enough — we also scan the final URL path and a sample of the body for known
    markers. YouTube wins on any youtube/googlevideo hint.
    """
    s = (final_url + " " + body[:1500]).lower()
    if any(k in s for k in ("youtube", "youtu.be", "googlevideo", "ytimg")):
        return "youtube"
    if "filmon" in s:
        return "filmon"
    if "pluto" in s:
        return "pluto"
    if "samsung" in s or "jmp2" in s:
        return "samsung"
    if "plex.tv" in s or "provider-static.plex" in s:
        return "plex"
    if "tubi" in s:
        return "tubi"
    return "other"


async def _probe_status(client: httpx.AsyncClient, url: str) -> dict:
    """Resolve one channel and classify it for the View modal.

    Returns ``{"status": ready|geo|dead, "source": <label>}``. ``geo`` is an
    HTTP 451 (blocked for legal reasons); ``ready`` resolves to a stream; every
    other outcome (404, auth, timeout…) is ``dead``.
    """
    try:
        async with client.stream("GET", url.replace(" ", "%20"), timeout=_PROBE_TIMEOUT) as r:
            final = str(r.url)
            if r.status_code == 451:
                return {"status": "geo", "source": _source_from(final)}
            if r.status_code != 200:
                return {"status": "dead", "source": _source_from(final)}
            ctype = r.headers.get("content-type", "").lower()
            # video, audio (radio/Icecast), or raw TS/binary stream → live.
            if any(k in ctype for k in ("video", "audio", "mpeg", "octet", "mp2t", "mpegts", "ogg", "aac", "icecast")):
                return {"status": "ready", "source": _source_from(final)}
            async for chunk in r.aiter_bytes():
                head = chunk[:2000].decode("utf-8", "replace")
                low = head.lower()
                # benmoose39's "moose" placeholder loop is what M3USe serves when
                # the real upstream is offline — a 200 that is NOT a live channel.
                if "moose-multiple" in low or "moose_offline" in low:
                    return {"status": "dead", "source": "other"}
                ok = ("#EXT" in head) or (".ts" in head) or (".m3u8" in head)
                return {"status": "ready" if ok else "dead", "source": _source_from(final, head)}
            return {"status": "dead", "source": _source_from(final)}
    except (httpx.HTTPError, UnicodeError):
        return {"status": "dead", "source": "other"}


async def _assess_channels(channels: list[dict]) -> tuple[int, int]:
    """Probe a spread-out sample of channels; return (alive, sampled)."""
    idxs = _sample_indices(len(channels), _PROBE_SAMPLE)
    if not idxs:
        return (0, 0)
    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def one(client: httpx.AsyncClient, url: str) -> bool:
        async with sem:
            return await _probe_channel(client, url)

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        results = await asyncio.gather(*(one(client, channels[i]["url"]) for i in idxs))
    return (sum(results), len(results))


# Playlists whose name starts with this share a dedupe group: a channel URL is
# kept only in the lowest-id (earliest-created, most specific) member and
# dropped from the others, so the same stream isn't listed/imported twice.
_DEDUPE_GROUP_PREFIX = "Kobe Store"


async def _dedupe_exclude_urls(db: AsyncSession, p: Playlist) -> set[str]:
    """URLs owned by a higher-priority member of this playlist's dedupe group.

    Membership is by name prefix; priority is by id (lower id wins). Returns an
    empty set for playlists outside the group, so nothing else is affected.
    """
    if not (p.name or "").startswith(_DEDUPE_GROUP_PREFIX):
        return set()
    others = (await db.execute(
        select(Playlist).where(
            Playlist.name.like(f"{_DEDUPE_GROUP_PREFIX}%"),
            Playlist.id < p.id,
        )
    )).scalars().all()
    urls: set[str] = set()
    for o in others:
        try:
            for c in _parse_m3u(await _fetch_m3u(o.url)):
                urls.add(c["url"])
        except (httpx.HTTPError, ValueError):
            continue  # a sibling being down shouldn't break this playlist
    return urls


async def _channels_for(db: AsyncSession, p: Playlist) -> list[dict]:
    """Parse a playlist's feed and return all channels (no dedup — each
    playlist keeps its full set regardless of overlap with siblings)."""
    return _parse_m3u(await _fetch_m3u(p.url))


async def _refresh_meta(p: Playlist, db: AsyncSession) -> None:
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
        channels = await _channels_for(db, p)
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("playlist %s refresh failed: %s", p.url, exc)
        p.last_error = "Could not fetch playlist (it may be down or invalid)."
        p.health = "feed offline"
        return

    p.channel_count = len(channels)
    p.logos = _sample_logos(channels)
    # Cache the channel list (capped) for the Channels page aggregation.
    p.channels = [
        {"name": c["name"], "logo": c.get("logo") or "", "url": c["url"], "category": c.get("category") or ""}
        for c in channels[:5000]
    ]

    if not channels:
        p.health = "no channels"
        p.last_error = "Playlist has no channels"
        return

    # Don't probe channels while a stream is playing — each probe opens another
    # upstream connection and a connection-limited account would reject the
    # player's stream ("multiple connections detected"). Keep the prior health.
    if ffmpeg_manager.active_stream_count() > 0:
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
            await _refresh_meta(p, db)
            await db.commit()
        if playlists:
            logger.info("Playlist health sweep refreshed %d playlists", len(playlists))


async def playlist_health_loop() -> None:
    """Background task: re-check all playlists daily so dead feeds surface on
    their own without the operator manually hitting Refresh.

    Skips while anything is playing — the sweep probes channels, which opens
    upstream connections that a connection-limited account can't spare without
    disrupting the live stream. When it skips it retries in a few minutes rather
    than waiting the full day.
    """
    await asyncio.sleep(_SWEEP_STARTUP_DELAY)
    while True:
        try:
            if ffmpeg_manager.active_stream_count() > 0:
                logger.info("Playlist health sweep skipped — a stream is active")
                await asyncio.sleep(_SWEEP_SKIP_RETRY)
                continue
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
    db.add(p)
    await db.flush()  # assign an id so dedupe-group priority (lower id wins) is correct
    await _refresh_meta(p, db)
    await db.commit()
    await db.refresh(p)
    return _serialize(p)


# ── Internal category feeds (cached in Redis) ─────────────────────────────

@router.get("/category-feed/{category}")
async def category_feed(category: str):
    """Serve a cached M3U feed for one category (built from us/eng/index)."""
    import json
    from app.redis_client import get_redis
    r = await get_redis()
    data = await r.get(f"category_feed:{category}")
    if not data:
        raise HTTPException(404, f"No cached feed for category {category!r}")
    channels = json.loads(data)
    lines = ["#EXTM3U"]
    for c in channels:
        name = c["name"].replace('"', "").replace("'", "")
        logo = (c.get("logo") or "").replace('"', "")
        cat = c.get("category") or ""
        lines.append(
            f'#EXTINF:-1 tvg-id="" '
            f'tvg-name="{name}" '
            f'tvg-logo="{logo}" '
            f'group-title="{cat}",{name}'
        )
        lines.append(c["url"])
    return Response(
        content="\n".join(lines),
        media_type="audio/x-mpegurl",
    )


# ── CRUD ──────────────────────────────────────────────────────────────────

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
    await _refresh_meta(p, db)
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
        channels = await _channels_for(db, p)
    except httpx.HTTPError as exc:
        logger.warning("playlist %s channel fetch failed: %s", p.url, exc)
        raise HTTPException(502, "Could not fetch playlist channels")
    return {"playlist_id": p.id, "name": p.name, "channels": channels}


@router.get("/{playlist_id}/channels/probe")
async def probe_playlist_channels(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Per-channel live status + source for the View modal.

    Returns a ``statuses`` array aligned to the same order as ``/channels``
    (both parse the feed identically), each ``{status, source}``. Probes run
    concurrently but capped, so a big playlist may take a while — the frontend
    renders the list first and fills these in when they land.
    """
    result = await db.execute(select(Playlist).where(Playlist.id == playlist_id))
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Playlist not found")

    # Don't probe while a stream is playing: each probe opens another upstream
    # connection, and a connection-limited account (M3USe trial) would reject the
    # player's own stream with "multiple connections detected". Signal the UI to
    # skip live status rather than disrupt playback.
    if ffmpeg_manager.active_stream_count() > 0:
        return {"playlist_id": p.id, "skipped": True, "statuses": []}

    try:
        channels = await _channels_for(db, p)
    except httpx.HTTPError as exc:
        logger.warning("playlist %s probe fetch failed: %s", p.url, exc)
        raise HTTPException(502, "Could not fetch playlist channels")

    sem = asyncio.Semaphore(_PROBE_CONCURRENCY)

    async def one(url: str) -> dict:
        async with sem:
            return await _probe_status(client, url)

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        statuses = await asyncio.gather(*(one(c["url"]) for c in channels))
    return {"playlist_id": p.id, "skipped": False, "statuses": statuses}


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

    # Save the M3U link for future re-use before cascading the delete.
    from app.models import Settings
    import json
    saved = await db.execute(
        select(Settings).where(Settings.key == "deleted_playlist_urls")
    )
    row = saved.scalar_one_or_none()
    archive: list = json.loads(row.value) if (row and row.value) else []
    # Keep at most 50 entries; newest first.
    archive.insert(0, {"name": p.name, "url": p.url, "deleted_at": datetime.now(timezone.utc).isoformat()})
    archive = archive[:50]
    if row:
        row.value = json.dumps(archive)
    else:
        db.add(Settings(key="deleted_playlist_urls", value=json.dumps(archive)))

    # Cascade: delete the category that was auto-created for this playlist
    # (same name), its bouquet references, and all streams imported into it.
    cat_result = await db.execute(
        select(StreamCategory).where(StreamCategory.name == p.name)
    )
    category = cat_result.scalar_one_or_none()
    if category:
        # Delete bouquet-category refs first (FK constraint)
        await db.execute(
            delete(BouquetCategory).where(BouquetCategory.category_id == category.id)
        )
        # Delete streams in this category
        await db.execute(
            delete(Stream).where(Stream.category_id == category.id)
        )
        # Delete the category itself
        await db.delete(category)

    await db.delete(p)
    await db.commit()


@router.get("/deleted/urls")
async def list_deleted_urls(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Recently-deleted playlist URLs available for one-click restoration."""
    import json
    from app.models import Settings
    saved = await db.execute(
        select(Settings).where(Settings.key == "deleted_playlist_urls")
    )
    row = saved.scalar_one_or_none()
    if not row or not row.value:
        return []
    try:
        return json.loads(row.value)
    except (ValueError, TypeError):
        return []
