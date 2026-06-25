"""
Unified channel directory (/api/channels).

Aggregates EVERY channel the operator has: imported streams plus every channel
from saved playlists (from the cached `Playlist.channels`, so no feeds are
re-fetched). Playlist channels already imported as a stream are deduped out.
The Channels page renders this; status is shown as online / offline / geo only.
"""
import hashlib
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_admin
from app.database import get_db
from app.ffmpeg_manager import ffmpeg_manager
from app.models import (
    AiEvent, Bouquet, BouquetCategory, ChannelHealth, Playlist, Stream, StreamCategory,
)
from app.sources import source_urls

router = APIRouter(prefix="/api/channels", tags=["channels"])
logger = logging.getLogger(__name__)


class ProbeIn(BaseModel):
    url: str
    name: str | None = None


def _key(prefix: str, val: str) -> str:
    return f"{prefix}_{hashlib.md5(val.encode()).hexdigest()[:10]}"


async def _premium_scope(db: AsyncSession) -> tuple[set[int], set[str]]:
    """Category ids + (lowercased) names belonging to the "Premium" bouquet.

    Used to keep premium content OUT of the general Channels directory — premium
    streams and premium playlists live only under the Premium pages. Resolved by
    bouquet name (case-insensitive); empty sets when there's no Premium bouquet.
    Inlined here (rather than importing premium.py) to avoid a circular import.
    """
    bouquet = (await db.execute(
        select(Bouquet).where(func.lower(Bouquet.name) == "premium")
    )).scalars().first()
    if not bouquet:
        return set(), set()
    rows = (await db.execute(
        select(StreamCategory.id, StreamCategory.name)
        .join(BouquetCategory, BouquetCategory.category_id == StreamCategory.id)
        .where(BouquetCategory.bouquet_id == bouquet.id)
    )).all()
    return {r[0] for r in rows}, {(r[1] or "").strip().lower() for r in rows}


async def build_channel_rows(db: AsyncSession, category_ids: set[int] | None = None) -> list[dict]:
    """Build the unified channel-directory rows.

    ``category_ids`` is None for the full directory (every imported stream plus
    all cached playlist channels). When a set is passed, only imported streams in
    those categories are returned and the playlist-channels block is skipped — the
    Premium channels page uses this to scope to a bouquet's categories. One source
    of truth so the health/status logic can't drift between the two views.
    """
    cats = {c.id: c.name for c in (await db.execute(select(StreamCategory))).scalars()}
    # For the full directory, exclude Premium content (it has its own pages).
    premium_cat_ids: set[int] = set()
    premium_names: set[str] = set()
    if category_ids is None:
        premium_cat_ids, premium_names = await _premium_scope(db)
    stream_q = select(Stream).options(selectinload(Stream.sources))
    if category_ids is not None:
        stream_q = stream_q.where(Stream.category_id.in_(category_ids))
    elif premium_cat_ids:
        stream_q = stream_q.where(Stream.category_id.notin_(premium_cat_ids))
    streams = (await db.execute(stream_q)).scalars().all()
    live = {s["stream_id"]: s for s in await ffmpeg_manager.get_all_statuses()}
    # Real probed health (online | offline | geo) keyed by URL, from the sweep.
    health = {r.url: r.status for r in (await db.execute(select(ChannelHealth))).scalars().all()}

    def resolve(primary_url: str | None, imported: bool, enabled: bool,
                live_status: str | None, viewers: int) -> str:
        # Ground truth first: a channel FFmpeg is actively streaming IS online,
        # whatever a stale probe says.
        if imported:
            if live_status in ("running", "starting") or viewers > 0:
                return "online"
            if not enabled:
                return "offline"
        if primary_url and primary_url in health:
            return health[primary_url]          # last probe result
        if imported and live_status == "error":
            return "offline"
        # Default: online if from a healthy playlist, pending if not yet refreshed.
        return "online" if not imported else "checking"

    out: list[dict] = []
    imported_urls: set[str] = set()
    for s in streams:
        urls = source_urls(s, s.sources) or ([s.stream_url] if s.stream_url else [])
        for u in urls:
            imported_urls.add(u)
        if s.stream_url:
            imported_urls.add(s.stream_url)
        live_status = live.get(s.id, {}).get("status", s.status)
        viewers = live.get(s.id, {}).get("viewer_count", 0)
        out.append({
            "key": f"s{s.id}",
            "stream_id": s.id,
            "name": s.name,
            "logo": s.logo_url or "",
            "source": cats.get(s.category_id) or "Streams",
            "imported": True,
            "is_enabled": s.is_enabled,
            "health": resolve(urls[0] if urls else None, True, s.is_enabled, live_status, viewers),
        })

    # Playlist channels (cached) not already imported, deduped across playlists.
    # Skipped for a category-scoped (Premium) view — those come from streams only.
    if category_ids is None:
        seen = set(imported_urls)
        for p in (await db.execute(select(Playlist))).scalars().all():
            if (p.name or "").strip().lower() in premium_names:
                continue                       # premium playlists live under Premium
            for c in (p.channels or []):
                url = c.get("url")
                if not url or url in seen:
                    continue
                seen.add(url)
                out.append({
                    "key": _key("p", url),
                    "stream_id": None,
                    "name": c.get("name") or "Unnamed",
                    "logo": c.get("logo") or "",
                    "source": p.name,
                    "imported": False,
                    "is_enabled": True,
                    "health": resolve(url, False, True, None, 0),
                    "url": url,
                })
    return out


@router.get("")
async def list_channels(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    return await build_channel_rows(db)


@router.post("/probe")
async def probe_channel(data: ProbeIn, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Check a single (non-imported) channel URL: online / geo / offline.
    Skips while a stream is playing so it can't trip a provider's connection limit."""
    if ffmpeg_manager.active_stream_count() > 0:
        return {"status": "skipped", "note": "A channel is playing — re-check when nothing is streaming."}
    from app.routers.playlists import _probe_status  # reuse the resolver/classifier

    async with httpx.AsyncClient(follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
        r = await _probe_status(client, data.url)
    status = {"ready": "online", "geo": "geo", "dead": "dead"}.get(r["status"], "offline")
    name = (data.name or "Channel").strip()

    # Persist so the card keeps the real status (and the sweep won't redo it soon).
    from datetime import datetime, timezone
    row = (await db.execute(select(ChannelHealth).where(ChannelHealth.url == data.url))).scalars().first()
    now = datetime.now(timezone.utc)
    if row:
        row.status, row.last_checked = status, now
    else:
        db.add(ChannelHealth(url=data.url, status=status, last_checked=now))
    note = {
        "online": "Source is live and reachable.",
        "geo": "Blocked in this region (HTTP 451).",
        "offline": "Source is down / unreachable.",
    }[status]
    db.add(AiEvent(kind="alert", title=f"{name}: {status}", detail=note,
                   data={"cause": "geo_blocked" if status == "geo" else status, "auto_applied": False}))
    await db.commit()
    return {"status": status, "source": r.get("source"), "note": note}


# ── 15-minute diagnostic sweep ────────────────────────────────────────────
# Probes all imported streams + previously-probed playlist URLs every 15 min.
# Fast HTTP-only probe (no ffprobe/FFmpeg) — full diagnostics available via
# the per-channel Diagnose button.

async def channels_diag_loop() -> None:
    """Fast sweep: probes all known URLs every 15 min at 50 concurrent."""
    import asyncio, httpx

    while True:
        try:
            from app.database import AsyncSessionLocal
            from app.models import Stream, ChannelHealth, Playlist
            from app.routers.playlists import _probe_status
            from sqlalchemy import select, func
            from datetime import datetime, timezone

            async with AsyncSessionLocal() as db:
                # All imported stream URLs
                streams = (await db.execute(
                    select(Stream.id, Stream.name, Stream.stream_url)
                    .where(Stream.is_enabled == True)
                )).all()

                # All previously-probed playlist channel URLs
                probed = (await db.execute(
                    select(ChannelHealth.url)
                )).scalars().all()

                # Also add a rotating sample of unprobed playlist channels
                pls = (await db.execute(select(Playlist))).scalars().all()
                unprobed_sample = []
                for pl in pls:
                    if not pl.channels:
                        continue
                    from app.routers.playlists import _sample_indices
                    idxs = _sample_indices(len(pl.channels), 10)  # 10 per playlist
                    for i in idxs:
                        url = pl.channels[i].get("url") if i < len(pl.channels) else None
                        if url and url not in probed:
                            unprobed_sample.append((0, pl.channels[i].get("name", "?"), url))

                all_targets = list(streams) + [(0, "", u) for u in probed] + unprobed_sample
                # Dedup by URL
                seen_urls = set()
                unique_targets = []
                for sid, name, url in all_targets:
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        unique_targets.append((sid, name, url))

                logger.info("Diag sweep: %d targets (%d streams + %d probed + %d new)",
                           len(unique_targets), len(streams), len(probed), len(unprobed_sample))

                sem = asyncio.Semaphore(50)
                async def probe_one(sid, name, url):
                    async with sem:
                        try:
                            async with httpx.AsyncClient(
                                timeout=8, follow_redirects=True,
                                headers={"User-Agent": "Mozilla/5.0"}
                            ) as client:
                                r = await _probe_status(client, url)
                            status = {"ready": "online", "geo": "geo", "dead": "dead"}.get(r.get("status", ""), "offline")
                            return (sid, name, url, status)
                        except Exception:
                            return (sid, name, url, "dead")

                tasks = [probe_one(sid, name, url) for sid, name, url in unique_targets]
                results = await asyncio.gather(*tasks)
                now = datetime.now(timezone.utc)
                counts = {"online": 0, "offline": 0, "geo": 0, "dead": 0}

                for sid, name, url, status in results:
                    counts[status] = counts.get(status, 0) + 1
                    existing = (await db.execute(
                        select(ChannelHealth).where(ChannelHealth.url == url)
                    )).scalar_one_or_none()
                    if existing:
                        existing.status = status
                        existing.last_checked = now
                    else:
                        db.add(ChannelHealth(url=url, status=status, last_checked=now))

                await db.commit()
                logger.info("Diag sweep done: %s", " ".join(f"{k}={v}" for k, v in sorted(counts.items())))

        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Diag sweep failed: %s", e)

        await asyncio.sleep(900)  # 15 minutes
