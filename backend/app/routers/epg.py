import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from lxml import etree
from app.auth import get_current_admin
from app.database import get_db, AsyncSessionLocal
from app.models import EpgSource, EpgData, Stream

router = APIRouter(prefix="/api/epg", tags=["epg"])
logger = logging.getLogger(__name__)


class EpgSourceCreate(BaseModel):
    name: str
    url: str
    update_interval_hours: int = 24


class EpgSourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    is_enabled: Optional[bool] = None
    update_interval_hours: Optional[int] = None


class EpgMapping(BaseModel):
    stream_id: int
    epg_channel_id: str


async def fetch_and_parse_epg(source_id: int, url: str):
    """Download XMLTV and parse into epg_data table."""
    logger.info(f"Fetching EPG from {url}")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                content = await resp.read()

        # Transparently handle gzipped feeds (.xml.gz, or any source serving
        # gzip without a decoded Content-Encoding). XMLTV starts with '<'.
        if content[:2] == b"\x1f\x8b":
            import gzip
            content = gzip.decompress(content)

        root = etree.fromstring(content)
        programmes = root.findall("programme")

        async with AsyncSessionLocal() as db:
            # Clear old data for this source
            await db.execute(delete(EpgData).where(EpgData.source_id == source_id))

            batch = []
            for prog in programmes:
                try:
                    start_str = prog.get("start", "")
                    stop_str = prog.get("stop", "")
                    channel = prog.get("channel", "")
                    title_el = prog.find("title")
                    desc_el = prog.find("desc")
                    cat_el = prog.find("category")

                    if not start_str or not stop_str or not channel:
                        continue

                    def parse_xmltv_time(s: str) -> datetime:
                        # Format: 20240101120000 +0000
                        s = s.strip()
                        if " " in s:
                            dt_str, tz_str = s.rsplit(" ", 1)
                        else:
                            dt_str, tz_str = s, "+0000"
                        dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
                        sign = 1 if tz_str[0] != "-" else -1
                        tz_str = tz_str.lstrip("+-")
                        h, m = int(tz_str[:2]), int(tz_str[2:])
                        from datetime import timedelta
                        offset = timedelta(hours=h, minutes=m) * sign
                        return (dt - offset).replace(tzinfo=timezone.utc)

                    batch.append(EpgData(
                        channel_id=channel,
                        title=title_el.text if title_el is not None else "Unknown",
                        description=desc_el.text if desc_el is not None else None,
                        start_time=parse_xmltv_time(start_str),
                        end_time=parse_xmltv_time(stop_str),
                        category=cat_el.text if cat_el is not None else None,
                        source_id=source_id,
                    ))

                    if len(batch) >= 500:
                        db.add_all(batch)
                        await db.flush()
                        batch = []

                except Exception as e:
                    logger.warning(f"EPG programme parse error: {e}")
                    continue

            if batch:
                db.add_all(batch)

            # Update source last_updated
            src_res = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
            src = src_res.scalar_one_or_none()
            if src:
                src.last_updated = datetime.now(timezone.utc)

            await db.commit()
            logger.info(f"EPG source {source_id} updated with {len(programmes)} entries")

    except Exception as e:
        logger.error(f"EPG fetch failed for source {source_id}: {e}")


async def epg_loop() -> None:
    """Re-fetch each enabled EPG source once its update_interval_hours elapses.

    XMLTV feeds only carry a few days of programmes, so a source must be
    refreshed periodically or the guide goes stale. We tick every 10 minutes
    and refresh any source whose last_updated is missing or older than its
    configured interval.
    """
    TICK = 600  # seconds between due-checks
    while True:
        try:
            now = datetime.now(timezone.utc)
            due: list[tuple[int, str]] = []
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(EpgSource).where(EpgSource.is_enabled == True)  # noqa: E712
                )
                for src in result.scalars().all():
                    interval = timedelta(hours=max(1, src.update_interval_hours or 24))
                    last = src.last_updated
                    if last is not None and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last is None or now - last >= interval:
                        due.append((src.id, src.url))

            for sid, url in due:
                await fetch_and_parse_epg(sid, url)
        except asyncio.CancelledError:
            break
        except Exception as e:  # never let the loop die on a transient error
            logger.error("EPG refresh loop failed: %s", e)
        await asyncio.sleep(TICK)


@router.get("/sources")
async def list_sources(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).order_by(EpgSource.id))
    return result.scalars().all()


@router.post("/sources", status_code=201)
async def add_source(
    data: EpgSourceCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    src = EpgSource(**data.model_dump())
    db.add(src)
    await db.commit()
    await db.refresh(src)
    # Kick off initial fetch in background
    background_tasks.add_task(fetch_and_parse_epg, src.id, src.url)
    return src


@router.post("/sources/{source_id}/refresh")
async def refresh_source(
    source_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "EPG source not found")
    background_tasks.add_task(fetch_and_parse_epg, src.id, src.url)
    return {"message": "EPG refresh started"}


@router.put("/sources/{source_id}")
async def update_source(
    source_id: int,
    data: EpgSourceUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "EPG source not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(src, k, v)
    await db.commit()
    await db.refresh(src)
    return src


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "EPG source not found")
    await db.delete(src)
    await db.commit()


@router.post("/map")
async def map_stream_to_epg(
    data: EpgMapping,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == data.stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    stream.epg_channel_id = data.epg_channel_id
    await db.commit()
    return {"ok": True}


@router.get("/now/{channel_id}")
async def get_now_playing(channel_id: str, db: AsyncSession = Depends(get_db)):
    """Get current EPG programme for a channel (public endpoint for players)."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(EpgData)
        .where(
            EpgData.channel_id == channel_id,
            EpgData.start_time <= now,
            EpgData.end_time >= now,
        )
        .limit(1)
    )
    prog = result.scalar_one_or_none()
    if not prog:
        return {}
    return {
        "title": prog.title,
        "description": prog.description,
        "start": prog.start_time,
        "end": prog.end_time,
        "category": prog.category,
    }
