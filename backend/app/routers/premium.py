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

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_current_admin
from app.database import get_db
from app.models import Bouquet, BouquetCategory, Playlist, StreamCategory
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
    """
    _ids, names = await _premium_categories(db)
    if not names:
        return []
    playlists = (await db.execute(select(Playlist))).scalars().all()
    return [_serialize_playlist(p) for p in playlists
            if (p.name or "").strip().lower() in names]


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
