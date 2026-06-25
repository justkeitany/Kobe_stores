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

import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Bouquet, BouquetCategory, Playlist, StreamCategory
from app.routers.channels import build_channel_rows
from app.routers.playlists import _channels_for, _serialize as _serialize_playlist

router = APIRouter(prefix="/api/premium", tags=["premium"])
logger = logging.getLogger(__name__)

_PREMIUM_NAME = "premium"


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
        select(Playlist).order_by(Playlist.created_at.desc(), Playlist.id.desc())
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
