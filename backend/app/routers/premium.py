"""
Premium section (/api/premium).

Surfaces the content of the bouquet named "Premium" as two views — channels and
playlists — for the dedicated Premium pages in the panel. Everything is resolved
by bouquet NAME (case-insensitive), never by hardcoded ids, so this works on any
deployment of the product; when no "Premium" bouquet exists the endpoints return
empty results and the UI shows a setup hint.

- Premium channels  = imported streams whose category is in the Premium bouquet.
- Premium playlists  = saved playlists whose name matches a premium category name
  (playlists aren't FK-linked to bouquets, so the name is the link). Their cards
  and View modal show the playlist's own source channels (the cached feed
  snapshot, falling back to a live parse) so they can be browsed and imported —
  exactly like the main Playlists page.
"""
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_admin
from app.database import get_db
from app.ffmpeg_manager import ffmpeg_manager
from app.models import Bouquet, BouquetCategory, Playlist, Stream, StreamCategory
from app.routers.channels import build_channel_rows
from app.routers.playlists import _channels_for, _refresh_meta, _serialize as _serialize_playlist
from app.routers.streams import _clean_source_list, _normalize_url, _replace_sources

router = APIRouter(prefix="/api/premium", tags=["premium"])
logger = logging.getLogger(__name__)

_PREMIUM_NAME = "premium"


def _norm_name(s: str) -> str:
    """Normalise a channel name for matching feed channels to imported streams."""
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _is_radio_playlist(name: str) -> bool:
    """Audio-only premium playlists (radio) don't get the forced video ladder."""
    return "radio" in (name or "").lower()


async def _premium_categories(db: AsyncSession) -> tuple[set[int], set[str]]:
    """Resolve the Premium bouquet's category ids and (lowercased) names.

    Returns empty sets when no bouquet named "Premium" exists.
    """
    bouquet = (await db.execute(
        select(Bouquet).where(func.lower(Bouquet.name) == _PREMIUM_NAME)
    )).scalars().first()
    if not bouquet:
        return set(), set()
    rows = (await db.execute(
        select(StreamCategory.id, StreamCategory.name)
        .join(BouquetCategory, BouquetCategory.category_id == StreamCategory.id)
        .where(BouquetCategory.bouquet_id == bouquet.id)
    )).all()
    ids = {r[0] for r in rows}
    names = {(r[1] or "").strip().lower() for r in rows}
    return ids, names


async def _playlist_source_channels(db: AsyncSession, p: Playlist) -> list[dict]:
    """A premium playlist's own source channels, in the modal/import row shape.

    Prefers the cached feed snapshot (``Playlist.channels``) — it loads instantly,
    survives deleting the imported streams, and doesn't hit an upstream the VPS
    may be IP-blocked from. Falls back to a live parse only when the cache is
    empty, and returns ``[]`` (never raises) if that also fails, so the modal
    shows an empty state instead of erroring.
    """
    def _row(c: dict) -> dict:
        return {
            "id": c.get("id") or "",
            "name": c.get("name") or "Unnamed",
            "category": c.get("category") or p.name,
            "logo": c.get("logo") or "",
            "url": c.get("url") or "",
        }

    cached = p.channels or []
    if cached:
        return [_row(c) for c in cached if c.get("url")]
    try:
        parsed = await _channels_for(db, p)
    except (httpx.HTTPError, ValueError):
        return []
    return [_row(c) for c in parsed if c.get("url")]


@router.get("/channels")
async def premium_channels(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Channels (imported streams) in the Premium bouquet's categories.

    Same row shape as /api/channels so the frontend reuses the Channel type.
    """
    ids, _names = await _premium_categories(db)
    if not ids:
        return []
    return await build_channel_rows(db, category_ids=ids)


@router.get("/playlists")
async def premium_playlists(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Saved playlists whose name matches a premium category name.

    Same JSON as /api/playlists so the frontend reuses the Playlist type + card.
    The channel_count / logos come straight from the playlist's cached feed
    metadata (its own source channels), so a playlist is listed and browsable
    even before — or after deleting — its imported streams.
    """
    _ids, names = await _premium_categories(db)
    if not names:
        return []
    playlists = (await db.execute(
        select(Playlist).order_by(func.lower(Playlist.name))
    )).scalars().all()
    return [
        _serialize_playlist(p)
        for p in playlists
        if (p.name or "").strip().lower() in names
    ]


@router.get("/playlists/{playlist_id}/channels")
async def premium_playlist_channels(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """A premium playlist's own source channels, ready to browse and import.

    Same `{channels: [...]}` shape the ChannelsModal expects, sourced from the
    cached feed snapshot (live parse fallback) — see _playlist_source_channels.
    """
    p = (await db.execute(select(Playlist).where(Playlist.id == playlist_id))).scalars().first()
    if not p:
        raise HTTPException(404, "Playlist not found")
    channels = await _playlist_source_channels(db, p)
    channels.sort(key=lambda c: (c["name"] or "").lower())
    return {"channels": channels}


@router.get("/summary")
async def premium_summary(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Counts + bouquet presence, for the pages' empty/setup states."""
    ids, names = await _premium_categories(db)
    has_bouquet = (await db.execute(
        select(func.count()).select_from(Bouquet).where(func.lower(Bouquet.name) == _PREMIUM_NAME)
    )).scalar() > 0
    channel_count = len(await build_channel_rows(db, category_ids=ids)) if ids else 0
    playlist_count = 0
    if names:
        playlists = (await db.execute(select(Playlist))).scalars().all()
        playlist_count = sum(1 for p in playlists if (p.name or "").strip().lower() in names)
    return {
        "has_bouquet": has_bouquet,
        "category_count": len(ids),
        "channel_count": channel_count,
        "playlist_count": playlist_count,
    }


async def _premium_playlists(db: AsyncSession) -> list[Playlist]:
    """Saved playlists whose name matches a Premium-bouquet category name."""
    _ids, names = await _premium_categories(db)
    if not names:
        return []
    playlists = (await db.execute(select(Playlist))).scalars().all()
    return [p for p in playlists if (p.name or "").strip().lower() in names]


@router.post("/sync")
async def premium_sync(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Re-sync premium channels IN PLACE from their playlist feeds.

    For each premium playlist: refresh its cached channel snapshot from the M3U
    feed, then reconcile the imported streams in the matching category — update a
    channel's source URL + logo in place when the feed has changed (this is what
    fixes channels stuck on the provider's looping filler when a stream ID goes
    stale), add channels that aren't imported yet, and flag video channels for the
    forced adaptive ladder (US/Canada; radio stays audio-only). Running streams
    whose source changed are restarted so the new URL/ladder take effect.
    """
    playlists = await _premium_playlists(db)
    if not playlists:
        raise HTTPException(400, "No Premium playlists found — set up the Premium bouquet first.")

    results: list[dict] = []
    to_restart: list[tuple[int, str, str, bool]] = []  # (stream_id, url, quality, force_adaptive)

    for p in playlists:
        await _refresh_meta(p, db)               # refresh cached feed from source
        feed = p.channels or []
        force_adaptive = not _is_radio_playlist(p.name)

        cat = (await db.execute(
            select(StreamCategory).where(func.lower(StreamCategory.name) == (p.name or "").strip().lower())
        )).scalars().first()

        existing: list[Stream] = []
        if cat:
            existing = (await db.execute(
                select(Stream).options(selectinload(Stream.sources)).where(Stream.category_id == cat.id)
            )).scalars().all()
        by_name: dict[str, Stream] = {}
        for s in existing:
            by_name.setdefault(_norm_name(s.name), s)

        # Apply the forced-ABR flag category-wide so EVERY premium TV channel gets
        # the ladder (US/Canada), not only the ones whose feed name still matches
        # the imported stream. Radio categories are forced off. Track restarts so a
        # running stream picks the change up immediately.
        for s in existing:
            if s.force_adaptive != force_adaptive:
                s.force_adaptive = force_adaptive
                cur = s.sources[0].url if s.sources else s.stream_url
                if cur:
                    to_restart.append((s.id, cur, s.quality, force_adaptive))

        updated = added = unchanged = 0
        for ch in feed:
            url = _normalize_url((ch.get("url") or "").strip())
            if not url:
                continue
            logo = (ch.get("logo") or "").strip()
            s = by_name.get(_norm_name(ch.get("name") or ""))
            if s:
                current = s.sources[0].url if s.sources else s.stream_url
                changed = False
                if current != url:
                    await _replace_sources(db, s, _clean_source_list([url]))
                    changed = True
                if logo and s.logo_url != logo:
                    s.logo_url = logo
                    changed = True
                if changed:
                    updated += 1
                    to_restart.append((s.id, url, s.quality, force_adaptive))
                else:
                    unchanged += 1
            elif cat:
                # New channel in the feed — import it into this category. Skip if
                # the URL already belongs to a stream (idempotent, no duplicates).
                dup = (await db.execute(select(Stream).where(Stream.stream_url == url))).scalars().first()
                if dup:
                    unchanged += 1
                    continue
                ns = Stream(
                    name=(ch.get("name") or "Unnamed"), logo_url=(logo or None),
                    category_id=cat.id, stream_url=url, quality="auto",
                    delivery_mode="restream", force_adaptive=force_adaptive,
                )
                db.add(ns)
                await db.flush()
                await _replace_sources(db, ns, _clean_source_list([url]))
                added += 1

        results.append({"name": p.name, "updated": updated, "added": added, "unchanged": unchanged})

    await db.commit()

    # Restart only the streams that actually changed; restart_stream is a cheap
    # no-op for streams that aren't currently running. Dedupe by stream id (a
    # stream can be queued by both the flag pass and the URL pass) — the later
    # entry wins so we restart with the freshest source URL.
    by_id = {sid: (url, quality, fa) for sid, url, quality, fa in to_restart}
    restarted = 0
    for sid, (url, quality, fa) in by_id.items():
        if await ffmpeg_manager.restart_stream(sid, [url], quality, force_adaptive=fa):
            restarted += 1

    return {"playlists": results, "restarted": restarted}


class PremiumPlaylistImport(BaseModel):
    name: str
    url: str
    description: str | None = None


@router.post("/playlists")
async def premium_playlist_import(
    data: PremiumPlaylistImport,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Import an M3U playlist into the Premium section.

    Creates (or updates) the saved playlist and files it under Premium by ensuring
    a category of the same name exists and is linked to the Premium bouquet — that
    name link is what makes a playlist 'premium'. Channels aren't imported as
    streams here; browse + Import (or Sync) does that, same as the main Playlists.
    """
    name = (data.name or "").strip()
    url = (data.url or "").strip()
    if not name:
        raise HTTPException(400, "Name is required")
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(400, "URL must start with http:// or https://")

    # Premium bouquet (create if it doesn't exist yet).
    bouquet = (await db.execute(
        select(Bouquet).where(func.lower(Bouquet.name) == _PREMIUM_NAME)
    )).scalars().first()
    if not bouquet:
        bouquet = Bouquet(name="Premium")
        db.add(bouquet)
        await db.flush()

    # Category of the same name (create if missing) — the premium link key.
    cat = (await db.execute(
        select(StreamCategory).where(func.lower(StreamCategory.name) == name.lower())
    )).scalars().first()
    if not cat:
        cat = StreamCategory(name=name)
        db.add(cat)
        await db.flush()

    # Link the category to the Premium bouquet (additive — keep existing links).
    linked = (await db.execute(
        select(BouquetCategory).where(
            BouquetCategory.bouquet_id == bouquet.id,
            BouquetCategory.category_id == cat.id,
        )
    )).scalars().first()
    if not linked:
        db.add(BouquetCategory(bouquet_id=bouquet.id, category_id=cat.id, sort_order=0))

    # Create or update the playlist, then refresh its cached channel snapshot.
    p = (await db.execute(
        select(Playlist).where(func.lower(Playlist.name) == name.lower())
    )).scalars().first()
    if p:
        p.url = url
        if data.description is not None:
            p.description = data.description.strip() or None
    else:
        p = Playlist(name=name, url=url, description=(data.description or "").strip() or None)
        db.add(p)
    await db.flush()
    await _refresh_meta(p, db)
    await db.commit()
    await db.refresh(p)
    return _serialize_playlist(p)


@router.get("/export")
async def premium_export_status(_=Depends(get_current_admin)):
    """Whether R2 backup is configured, and the most recent backups (presigned
    download URLs, so a private bucket still works)."""
    from app.r2_export import r2_configured, list_premium_backups
    if not r2_configured():
        return {"configured": False, "backups": []}
    try:
        backups = await list_premium_backups()
    except Exception as e:
        logger.warning("R2 backup listing failed: %s", e)
        return {"configured": True, "backups": [], "error": str(e)}
    return {"configured": True, "backups": backups}


@router.post("/export")
async def premium_export_run(_=Depends(get_current_admin)):
    """Export the premium playlists to R2 now (manual trigger)."""
    from app.r2_export import r2_configured, export_premium_to_r2
    if not r2_configured():
        raise HTTPException(400, "R2 is not configured. Set R2_* in backend/.env.")
    try:
        return await export_premium_to_r2()
    except Exception as e:
        logger.error("Manual R2 export failed: %s", e)
        raise HTTPException(502, f"Export failed: {e}")
