"""
Live TV channel sync for Premium (cdnlivetv.tv / ntv.cx integration).

Imports ~450 channels from ntv.cx (cdnlive backend) into country-based StreamCategory
groups under the Premium bouquet. Tokens expire in 3h; this module runs:
  - Full catalog sync: startup + every 6h (new channels, metadata updates)
  - Token refresh: every 90 min, restart running cdnlive streams with < 30 min left
    on their cached token → re-mint happens on restart, keeping viewers uninterrupted
"""
import asyncio
import logging
import re
from datetime import datetime, timezone

import httpx
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_session
from app.models import Bouquet, BouquetCategory, Stream, StreamCategory
from app.category_sync import link_category_to_all_bouquets
from app.routers.streams import _replace_sources
from app.ffmpeg_manager import ffmpeg_manager
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

_CHANNELS_API = "https://ntv.cx/api/get-channels"
_REFERER = "https://ntv.cx/"
_UA = "Mozilla/5.0 AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
_PREMIUM_BOUQUET = "premium"
_TOKEN_EXPIRY_BUFFER = 1800  # 30 min in seconds

# Country code → display name
CODE2NAME = {
    "ae": "United Arab Emirates", "ar": "Argentina", "at": "Austria",
    "au": "Australia", "be": "Belgium", "bg": "Bulgaria", "br": "Brazil",
    "ca": "Canada", "cl": "Chile", "cy": "Cyprus", "cz": "Czechia",
    "de": "Germany", "dk": "Denmark", "es": "Spain", "fr": "France",
    "gb": "United Kingdom", "gr": "Greece", "hr": "Croatia", "il": "Israel",
    "in": "India", "it": "Italy", "mx": "Mexico", "nl": "Netherlands",
    "nz": "New Zealand", "pl": "Poland", "pt": "Portugal", "ro": "Romania",
    "rs": "Serbia", "ru": "Russia", "sa": "Saudi Arabia", "se": "Sweden",
    "tr": "Turkey", "us": "United States", "uy": "Uruguay",
}

# Keyword → country (for empty-code channels with country in the name)
_COUNTRY_KW = [
    ("USA", "United States"), ("US", "United States"), ("UK", "United Kingdom"),
    ("England", "United Kingdom"), ("Spain", "Spain"), ("Portugal", "Portugal"),
    ("Serbia", "Serbia"), ("Croatia", "Croatia"), ("UAE", "United Arab Emirates"),
    ("Germany", "Germany"), ("DE", "Germany"), ("France", "France"),
    ("FR", "France"), ("Italy", "Italy"), ("Netherlands", "Netherlands"),
    ("Poland", "Poland"), ("Brazil", "Brazil"), ("Mexico", "Mexico"),
    ("Argentina", "Argentina"), ("Australia", "Australia"), ("Canada", "Canada"),
    ("Greece", "Greece"), ("Turkey", "Turkey"), ("Russia", "Russia"),
    ("India", "India"), ("Pakistan", "Pakistan"), ("Romania", "Romania"),
    ("Bulgaria", "Bulgaria"), ("Denmark", "Denmark"), ("Sweden", "Sweden"),
    ("Israel", "Israel"), ("Saudi", "Saudi Arabia"), ("Austria", "Austria"),
    ("Belgium", "Belgium"), ("Czech", "Czechia"), ("Chile", "Chile"),
]


def _infer_country(name: str) -> str:
    """Infer country from channel name when channel_code is empty."""
    for kw, country in _COUNTRY_KW:
        if re.search(r"(?<![A-Za-z])" + kw + r"(?![A-Za-z])", name, re.I):
            return country
    return "International"


def _country_of(ch: dict) -> str:
    code = (ch.get("channel_code") or "").lower()
    if code in CODE2NAME:
        return CODE2NAME[code]
    if code:
        return code.upper()
    return _infer_country(ch.get("channel_name", ""))


async def sync_cdnlive(db: AsyncSession) -> dict:
    """Import/update cdnlive channels from ntv.cx into Premium country categories.

    Returns {added, updated, unchanged, countries, changed_stream_ids}.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(_CHANNELS_API, headers={"Referer": _REFERER, "User-Agent": _UA})
        if resp.status_code != 200:
            logger.error("cdnlive sync: get-channels HTTP %s", resp.status_code)
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json()
    except Exception as e:
        logger.error("cdnlive sync: fetch failed: %s", e)
        return {"error": str(e)}

    channels = [
        ch for ch in data.get("channels", [])
        if ch.get("server") == "cdnlive" and ch.get("channel_url")
    ]
    logger.info("cdnlive sync: fetched %d cdnlive channels", len(channels))

    # Ensure Premium bouquet
    bouquet = (await db.execute(
        select(Bouquet).where(func.lower(Bouquet.name) == _PREMIUM_BOUQUET)
    )).scalars().first()
    if not bouquet:
        bouquet = Bouquet(name="Premium", description="Premium live TV channels")
        db.add(bouquet)
        await db.flush()

    # Group by country
    by_country: dict[str, list] = {}
    for ch in channels:
        country = _country_of(ch)
        by_country.setdefault(country, []).append(ch)

    added = updated = unchanged = 0
    changed_stream_ids = []

    for country, country_channels in by_country.items():
        # Ensure category
        cat = (await db.execute(
            select(StreamCategory).where(func.lower(StreamCategory.name) == country.lower())
        )).scalars().first()
        if not cat:
            cat = StreamCategory(name=country, sort_order=0)
            db.add(cat)
            await db.flush()
            # Link to Premium bouquet + all other bouquets so users see it
            link_rec = (await db.execute(
                select(BouquetCategory).where(
                    BouquetCategory.bouquet_id == bouquet.id,
                    BouquetCategory.category_id == cat.id,
                )
            )).scalars().first()
            if not link_rec:
                db.add(BouquetCategory(bouquet_id=bouquet.id, category_id=cat.id, sort_order=0))
            await link_category_to_all_bouquets(db, cat.id, sort_order=0)

        # Upsert channels
        for ch in country_channels:
            name = (ch.get("channel_name") or "").strip()
            if not name:
                continue
            url = ch.get("channel_url")
            logo = ch.get("channel_image") or ""

            existing = (await db.execute(
                select(Stream).where(Stream.stream_url == url)
            )).scalars().first()

            if existing:
                changed = False
                if existing.name != name:
                    existing.name = name
                    changed = True
                if existing.logo_url != logo:
                    existing.logo_url = logo
                    changed = True
                if existing.category_id != cat.id:
                    existing.category_id = cat.id
                    changed = True
                if not existing.force_adaptive:
                    existing.force_adaptive = True
                    changed = True
                if changed:
                    updated += 1
                    if existing.status == "running":
                        changed_stream_ids.append(existing.id)
                else:
                    unchanged += 1
            else:
                stream = Stream(
                    name=name,
                    stream_url=url,
                    logo_url=logo,
                    category_id=cat.id,
                    delivery_mode="restream",
                    quality="auto",
                    force_adaptive=True,
                    is_enabled=True,
                )
                db.add(stream)
                await db.flush()
                await _replace_sources(db, stream, [url])
                added += 1

    await db.commit()
    logger.info(
        "cdnlive sync: added=%d updated=%d unchanged=%d countries=%d changed_running=%d",
        added, updated, unchanged, len(by_country), len(changed_stream_ids),
    )
    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "countries": len(by_country),
        "changed_stream_ids": changed_stream_ids,
    }


async def _refresh_expiring_tokens():
    """Restart running cdnlive streams whose cached token is near expiry (< 30 min).

    Resolution re-mints on every FFmpeg (re)start; this keeps live viewers from
    hitting a token-expiry 403 mid-watch. The 90-min loop interval + 30-min buffer
    means every token gets refreshed ~1h before it dies (tokens last 3h).
    """
    try:
        r = await get_redis()
        # Scan for cdnlive:* keys (cached resolved URLs)
        cursor = 0
        near_expiry = []
        while True:
            cursor, keys = await r.scan(cursor, match="cdnlive:*", count=100)
            for key in keys:
                ttl = await r.ttl(key)
                if 0 < ttl < _TOKEN_EXPIRY_BUFFER:
                    near_expiry.append(key)
            if cursor == 0:
                break

        if not near_expiry:
            return

        logger.info("cdnlive token refresh: %d cached URLs near expiry", len(near_expiry))

        # Map cache keys back to player URLs, then to running stream ids
        # (cache key = cdnlive:<sha1(player_url)>; we need to look up streams by stream_url)
        # Simplified: just restart all running cdnlive streams — it's a small set
        async with get_session() as db:
            streams = (await db.execute(
                select(Stream.id, Stream.stream_url).where(
                    Stream.status == "running",
                    Stream.stream_url.like("%cdnlivetv.tv/api/v1/channels/player/%"),
                )
            )).all()

        restarted = 0
        for stream_id, _url in streams:
            success = await ffmpeg_manager.restart_stream(stream_id)
            if success:
                restarted += 1
                await asyncio.sleep(0.5)  # stagger restarts

        if restarted:
            logger.info("cdnlive token refresh: restarted %d streams", restarted)

    except Exception as e:
        logger.warning("cdnlive token refresh failed: %s", e)


async def livetv_sync_loop():
    """Background loop: full catalog sync every 6h + token refresh every 90 min."""
    await asyncio.sleep(10)  # let the app finish startup

    last_full_sync = datetime.min.replace(tzinfo=timezone.utc)
    last_token_refresh = datetime.min.replace(tzinfo=timezone.utc)
    full_sync_interval = 6 * 3600  # 6h in seconds
    token_refresh_interval = 90 * 60  # 90 min

    while True:
        try:
            now = datetime.now(timezone.utc)

            # Full catalog sync
            if (now - last_full_sync).total_seconds() >= full_sync_interval:
                logger.info("cdnlive: starting full catalog sync")
                async with get_session() as db:
                    result = await sync_cdnlive(db)
                # Restart changed streams
                if result.get("changed_stream_ids"):
                    for sid in result["changed_stream_ids"]:
                        await ffmpeg_manager.restart_stream(sid)
                        await asyncio.sleep(0.5)
                last_full_sync = now

            # Token refresh
            if (now - last_token_refresh).total_seconds() >= token_refresh_interval:
                await _refresh_expiring_tokens()
                last_token_refresh = now

        except Exception as e:
            logger.error("cdnlive loop error: %s", e)

        await asyncio.sleep(60)  # check every minute
