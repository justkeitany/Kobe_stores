"""Read-only EPG mapping diagnostic."""
import asyncio
from sqlalchemy import select, func
from app.database import AsyncSessionLocal
from app.models import Stream, EpgData
from app.routers.epg import automap


async def main() -> None:
    async with AsyncSessionLocal() as db:
        total = (await db.execute(select(func.count()).select_from(Stream))).scalar()
        mapped = (await db.execute(
            select(func.count()).select_from(Stream).where(
                Stream.epg_channel_id.isnot(None), Stream.epg_channel_id != ""
            )
        )).scalar()
        epg_rows = (await db.execute(select(func.count()).select_from(EpgData))).scalar()
        epg_chans = (await db.execute(
            select(func.count(func.distinct(EpgData.channel_id)))
        )).scalar()
        print(f"streams: {total} total, {mapped} mapped, {total - mapped} unmapped")
        print(f"epg: {epg_rows} programmes across {epg_chans} channels")

        # Sample some unmapped stream names
        names = (await db.execute(
            select(Stream.name).where(
                (Stream.epg_channel_id.is_(None)) | (Stream.epg_channel_id == "")
            ).limit(15)
        )).scalars().all()
        print("sample unmapped stream names:")
        for n in names:
            print(f"   - {n}")

        # Dry-run automap to see what it would match / miss
        r = await automap(only_unmapped=True, dry_run=True, db=db, _=None)
        print(f"dry-run automap: would match {r.matched} of {r.considered}")
        print("match samples:", r.samples[:8])
        print("unmatched sample:", r.unmatched[:15])


if __name__ == "__main__":
    asyncio.run(main())
