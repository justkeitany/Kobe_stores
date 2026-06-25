"""
Premium section (/api/premium).

Surfaces the content of the bouquet named "Premium" as two views — channels and
playlists — for the dedicated Premium pages in the panel. Everything is resolved
by bouquet NAME (case-insensitive), never by hardcoded ids, so this works on any
deployment of the product; when no "Premium" bouquet exists the endpoints return
empty results and the UI shows a setup hint.

- Premium channels  = imported streams whose category is in the Premium bouquet.
- Premium playlists  = saved playlists whose name matches a premium category name
  (playlists aren't FK-linked to bouquets, so the name is the link).
"""
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Bouquet, BouquetCategory, Playlist, Stream, StreamCategory
from app.routers.channels import build_channel_rows
from app.routers.playlists import _serialize as _serialize_playlist

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


async def _streams_by_premium_name(db: AsyncSession) -> dict[str, list[Stream]]:
    """Map each premium category name (lowercased) → its imported streams.

    Premium playlists are presented as the imported channels in the matching
    Premium category (resolved by name), so the cards/modal reflect the real
    deduped channel set rather than the raw m3u feed (which may be empty or
    IP-blocked). Empty dict when there's no Premium bouquet.
    """
    bouquet = (await db.execute(
        select(Bouquet).where(func.lower(Bouquet.name) == _PREMIUM_NAME)
    )).scalars().first()
    if not bouquet:
        return {}
    cat_rows = (await db.execute(
        select(StreamCategory.id, StreamCategory.name)
        .join(BouquetCategory, BouquetCategory.category_id == StreamCategory.id)
        .where(BouquetCategory.bouquet_id == bouquet.id)
    )).all()
    if not cat_rows:
        return {}
    cat_ids = [r[0] for r in cat_rows]
    streams = (await db.execute(
        select(Stream).where(Stream.category_id.in_(cat_ids))
    )).scalars().all()
    by_cat: dict[int, list[Stream]] = defaultdict(list)
    for s in streams:
        by_cat[s.category_id].append(s)
    return {(name or "").strip().lower(): by_cat.get(cid, [])
            for cid, name in cat_rows}


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

    Same JSON as /api/playlists so the frontend reuses the Playlist type + card,
    but the channel_count / logos are taken from the imported streams in the
    matching Premium category (the real, deduped channel set) rather than the raw
    m3u feed — which for some premium feeds is empty or IP-blocked.
    """
    streams_by_name = await _streams_by_premium_name(db)
    if not streams_by_name:
        return []
    playlists = (await db.execute(select(Playlist))).scalars().all()
    out = []
    for p in playlists:
        key = (p.name or "").strip().lower()
        if key not in streams_by_name:
            continue
        streams = streams_by_name[key]
        d = _serialize_playlist(p)
        d["channel_count"] = len(streams)
        d["logos"] = [s.logo_url for s in streams if s.logo_url][:5]
        d["health"] = None              # count comes from streams, not a feed probe
        d["last_error"] = None
        out.append(d)
    return out


@router.get("/playlists/{playlist_id}/channels")
async def premium_playlist_channels(
    playlist_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Channels of a premium playlist = imported streams in the matching Premium
    category. Same `{channels: [...]}` shape the ChannelsModal expects."""
    p = (await db.execute(select(Playlist).where(Playlist.id == playlist_id))).scalars().first()
    if not p:
        raise HTTPException(404, "Playlist not found")
    streams_by_name = await _streams_by_premium_name(db)
    streams = streams_by_name.get((p.name or "").strip().lower(), [])
    channels = [{
        "id": str(s.id),
        "name": s.name,
        "category": p.name,
        "logo": s.logo_url or "",
        "url": s.stream_url or "",
    } for s in sorted(streams, key=lambda s: (s.name or "").lower())]
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
