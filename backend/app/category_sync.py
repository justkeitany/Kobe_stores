"""
Keep new categories visible to existing users automatically.

A user only sees the categories in their assigned bouquet (package). Without
this, a freshly created category — e.g. one made while importing Plex/Samsung/
Roku/Tubi/Pluto channels — would be invisible to every user on a bouquet until
an admin edited that bouquet or recreated the users. We instead link each new
category into every existing bouquet at creation time, so imported channels
reach users immediately (the Xtream API reads bouquets live, with no caching).
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Bouquet, BouquetCategory


async def link_category_to_all_bouquets(
    db: AsyncSession, category_id: int, sort_order: int = 0
) -> None:
    """Add ``category_id`` to every bouquet that doesn't already list it.

    Idempotent and commit-neutral: the caller owns the surrounding transaction.
    """
    bouquet_ids = (await db.execute(select(Bouquet.id))).scalars().all()
    if not bouquet_ids:
        return
    already = set(
        (
            await db.execute(
                select(BouquetCategory.bouquet_id).where(
                    BouquetCategory.category_id == category_id
                )
            )
        ).scalars().all()
    )
    for bid in bouquet_ids:
        if bid not in already:
            db.add(
                BouquetCategory(
                    bouquet_id=bid, category_id=category_id, sort_order=sort_order
                )
            )
