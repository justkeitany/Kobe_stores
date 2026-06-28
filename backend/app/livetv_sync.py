"""
Live TV channel sync for Premium (cdnlivetv.tv / ntv.cx integration).

Builds per-country Premium *playlists* from the ntv.cx (cdnlive) catalog — staging
playlists the admin picks channels from and imports (no streams are auto-created).
Tokens expire in 3h; this module runs:
  - Full catalog refresh: startup + every 6h (new channels, fresh logos) — rebuilds
    the playlist snapshots only, never touches imported streams
  - Token refresh: every 90 min, restart running cdnlive streams with < 30 min left
    on their cached token → re-mint happens on restart, keeping viewers uninterrupted
"""
import asyncio
import logging
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import AsyncSessionLocal
from app.models import Bouquet, BouquetCategory, Playlist, Stream, StreamCategory
from app.ffmpeg_manager import ffmpeg_manager
from app.redis_client import get_redis
from app.config import settings

logger = logging.getLogger(__name__)

# Direct cdnlivetv.tv catalog (the source of truth: 515 channels / 35 countries,
# incl. South Africa). The ntv.cx mirror only carried a 450-channel subset and
# tagged the rest as unplayable 'dlhd'. user=cdnlivetv mints the same HLS the
# resolver already handles.
_CHANNELS_API = "https://api.cdnlivetv.tv/api/v1/channels/?user=cdnlivetv&plan=free"
_REFERER = "https://cdnlivetv.tv/"
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
    "tr": "Turkey", "us": "United States", "uy": "Uruguay", "za": "South Africa",
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


# Locally-hosted, extracted channel logos (provider images are 401/missing for a
# browser <img>, so we mirror them under /var/iptv/logos and serve at /logos/).
_LOGO_DIR = os.environ.get("IPTV_LOGO_DIR", "/var/iptv/logos")
_LOGO_CT = {"webp": "image/webp", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}


def _upload_logo_r2(key: str, content: bytes, ext: str) -> None:
    """Best-effort upload of a logo to R2 (so the public R2 URL resolves). No-op
    if R2 isn't configured; never raises into the sync."""
    if not (settings.R2_BUCKET and settings.R2_ACCESS_KEY_ID):
        return
    try:
        from app.r2_export import _r2_client
        _r2_client().put_object(
            Bucket=settings.R2_BUCKET, Key=key, Body=content,
            ContentType=_LOGO_CT.get(ext, "application/octet-stream"),
            CacheControl="public, max-age=604800",
        )
    except Exception as e:
        logger.debug("logo R2 upload failed for %s: %s", key, e)


def _logo_base() -> str:
    """Public base for serving logos: the R2 bucket's public domain when set
    (offloads the VPS, absolute URL works in M3U/Xtream too), else the local
    nginx /logos/ path."""
    base = (settings.R2_PUBLIC_BASE or "").rstrip("/")
    return f"{base}/logos" if base else "/logos"


def _local_logo(image_url: str) -> str:
    """Map a provider image URL to our hosted logo URL (R2 if configured, else
    local /logos/) when we have the file on disk; '' otherwise (clean placeholder,
    never a broken image). The provider's own URLs return 401 to a browser."""
    try:
        parts = [x for x in urlparse(image_url).path.split("/") if x]
    except Exception:
        return ""
    if len(parts) < 2:
        return ""
    country, fname = parts[-2], parts[-1]
    base = fname.rsplit(".", 1)[0]
    for ext in ("webp", "png", "jpg", "jpeg"):
        if os.path.exists(os.path.join(_LOGO_DIR, country, f"{base}.{ext}")):
            return f"{_logo_base()}/{country}/{base}.{ext}"
    return ""


async def _ensure_logos(channels: list[dict]) -> int:
    """Download any not-yet-cached channel logos into ``_LOGO_DIR`` so they can be
    served locally. The provider lists images on cdnlivetv.tv (401 to a browser)
    but serves them on api.cdnlivetv.tv; the listed extension is often wrong
    (.webp vs .png), so we try a few. Skips files already on disk → cheap on
    repeat syncs. Never raises (a logo failure must not break the catalog sync).
    """
    seen: set[tuple[str, str]] = set()
    fetched = 0
    sem = asyncio.Semaphore(12)
    headers = {"Referer": "https://cdnlivetv.tv/", "User-Agent": _UA}

    async def grab(client: httpx.AsyncClient, image_url: str):
        nonlocal fetched
        path = urlparse(image_url).path
        parts = [x for x in path.split("/") if x]
        if len(parts) < 2:
            return
        country, fname = parts[-2], parts[-1]
        base = fname.rsplit(".", 1)[0]
        if (country, base) in seen:
            return
        seen.add((country, base))
        for e in ("webp", "png", "jpg", "jpeg"):
            if os.path.exists(os.path.join(_LOGO_DIR, country, f"{base}.{e}")):
                return
        api_base = "https://api.cdnlivetv.tv" + path.rsplit(".", 1)[0]
        ext0 = fname.rsplit(".", 1)[-1]
        for e in [ext0] + [x for x in ("webp", "png", "jpg") if x != ext0]:
            try:
                async with sem:
                    r = await client.get(f"{api_base}.{e}")
                if r.status_code == 200 and len(r.content) > 100:
                    dst = os.path.join(_LOGO_DIR, country, f"{base}.{e}")
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    with open(dst, "wb") as f:
                        f.write(r.content)
                    _upload_logo_r2(f"logos/{country}/{base}.{e}", r.content, e)
                    fetched += 1
                    return
            except Exception:
                pass

    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers, follow_redirects=True) as client:
            await asyncio.gather(*[grab(client, ch.get("channel_image") or "") for ch in channels])
        if fetched:
            logger.info("cdnlive sync: extracted %d new logos", fetched)
    except Exception as e:
        logger.warning("cdnlive sync: logo extraction skipped: %s", e)
    return fetched


_DEAD_HTTP = {403, 404, 410, 451, 500, 502, 503}


async def _probe_alive(client: httpx.AsyncClient, player_url: str, force: bool = False) -> bool:
    """True if a channel currently yields a live playlist; False on a confirmed
    dead/blocked upstream. Transient errors (timeout/network) count as alive so a
    blip never prunes a good channel."""
    from app.cdnlive_stream import resolve
    r = await resolve(player_url, force=force)
    if not r:
        return False
    try:
        resp = await client.get(r)
    except Exception:
        return True  # benefit of the doubt on a transient error
    if resp.status_code == 200:
        return any(l and not l.startswith("#") for l in resp.text.splitlines())
    if resp.status_code in _DEAD_HTTP:
        return False
    return True


async def _filter_alive(channels: list[dict]) -> list[dict]:
    """Drop channels whose upstream is confirmed dead/blocked, so the catalog
    shows mostly-working channels. Two-pass to avoid rate-limit false positives:
    a gentle first pass flags suspects, then suspects are re-probed individually
    (forcing a fresh mint) and only consistent failures are dropped. Recovered
    channels reappear on the next 6h sync. Never raises."""
    headers = {"Referer": "https://cdnlivetv.tv/", "User-Agent": _UA}
    try:
        # Pass 1 — gentle (concurrency 5).
        sem = asyncio.Semaphore(5)
        suspect: list[dict] = []
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            async def p1(ch):
                async with sem:
                    if not await _probe_alive(client, ch["channel_url"]):
                        suspect.append(ch)
            await asyncio.gather(*[p1(ch) for ch in channels])

        if not suspect:
            return channels

        # Pass 2 — re-probe only suspects, slow (concurrency 2) + fresh mint.
        sem2 = asyncio.Semaphore(2)
        dead: set[str] = set()
        async with httpx.AsyncClient(timeout=15.0, headers=headers, follow_redirects=True) as client:
            async def p2(ch):
                async with sem2:
                    await asyncio.sleep(0.3)
                    if not await _probe_alive(client, ch["channel_url"], force=True):
                        dead.add(ch["channel_url"])
            await asyncio.gather(*[p2(ch) for ch in suspect])

        alive = [ch for ch in channels if ch["channel_url"] not in dead]
        logger.info("cdnlive sync: health filter kept %d / %d (dropped %d dead)",
                    len(alive), len(channels), len(channels) - len(alive))
        return alive
    except Exception as e:
        logger.warning("cdnlive sync: health filter skipped: %s", e)
        return channels


def _category_name(country: str) -> str:
    """Premium playlist + category name for a country's live-TV channels.

    Suffixed with "Live TV" so it never collides with the user's own curated
    premium playlists (e.g. "United States", "Canada").
    """
    return f"{country} Live TV"


async def sync_cdnlive(db: AsyncSession, prune_dead: bool = True) -> dict:
    """Build per-country Premium *playlists* from the ntv.cx (cdnlive) catalog.

    These are staging playlists — the admin opens one, picks the channels they
    want, and imports them (the normal premium-playlist flow). We do NOT create
    live Streams here; that's the user's choice via the import modal.

    For each country we upsert:
      - a Playlist row whose cached ``channels`` snapshot holds every channel
        (name + logo + player URL), so its card shows logos and is browsable
        instantly without re-fetching upstream.
      - an (initially empty) StreamCategory of the same name linked to the
        Premium bouquet, so the playlist qualifies as "premium" and its card
        appears on the Premium → Playlists page.

    Returns {playlists, channels, countries}.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(_CHANNELS_API, headers={
                "Referer": _REFERER, "Origin": "https://cdnlivetv.tv", "User-Agent": _UA,
            })
        if resp.status_code != 200:
            logger.error("cdnlive sync: channels API HTTP %s", resp.status_code)
            return {"error": f"HTTP {resp.status_code}"}
        data = resp.json()
    except Exception as e:
        logger.error("cdnlive sync: fetch failed: %s", e)
        return {"error": str(e)}

    # Normalise to the field names the rest of this module uses. The direct API
    # returns {name, code, url, image}; the old ntv.cx mirror used channel_*.
    channels = []
    for ch in data.get("channels", []):
        url = ch.get("url") or ch.get("channel_url")
        if not url:
            continue
        channels.append({
            "channel_name": ch.get("name") or ch.get("channel_name") or "",
            "channel_code": ch.get("code") or ch.get("channel_code") or "",
            "channel_url": url,
            "channel_image": ch.get("image") or ch.get("channel_image") or "",
        })
    logger.info("cdnlive sync: fetched %d channels", len(channels))

    # Extract logos to local disk first, so the snapshot below maps each channel
    # to its hosted logo URL (provider image URLs 401 in a browser).
    await _ensure_logos(channels)

    # Drop channels whose upstream is confirmed dead/blocked so the catalog shows
    # mostly-working channels (recovered ones return on the next sync).
    if prune_dead:
        channels = await _filter_alive(channels)

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
        by_country.setdefault(_country_of(ch), []).append(ch)

    now = datetime.now(timezone.utc)
    playlists_upserted = 0
    total_channels = 0

    for country, country_channels in by_country.items():
        name = _category_name(country)

        # Build the cached channel snapshot (the import/modal row shape).
        snapshot = []
        for ch in country_channels:
            cn = (ch.get("channel_name") or "").strip()
            url = ch.get("channel_url")
            if not cn or not url:
                continue
            snapshot.append({
                "id": "",
                "name": cn,
                "logo": _local_logo(ch.get("channel_image") or ""),
                "url": url,
                "category": name,
            })
        if not snapshot:
            continue
        total_channels += len(snapshot)
        sample_logos = [c["logo"] for c in snapshot if c["logo"]][:6]
        description = (
            f"{country} live TV — {len(snapshot)} channels (cdnlive). "
            "Open to pick the channels you want and import them."
        )

        # Upsert the Playlist (matched by name).
        pl = (await db.execute(
            select(Playlist).where(func.lower(Playlist.name) == name.lower())
        )).scalars().first()
        if not pl:
            pl = Playlist(name=name, url=f"{_CHANNELS_API}#{country}")
            db.add(pl)
        pl.description = description
        pl.channel_count = len(snapshot)
        pl.logos = sample_logos
        pl.channels = snapshot
        pl.health = None
        pl.last_refreshed = now
        pl.last_error = None
        playlists_upserted += 1

        # Ensure a matching Premium category exists (empty until the user imports)
        # so the playlist shows up on the Premium page.
        cat = (await db.execute(
            select(StreamCategory).where(func.lower(StreamCategory.name) == name.lower())
        )).scalars().first()
        if not cat:
            cat = StreamCategory(name=name, sort_order=0)
            db.add(cat)
            await db.flush()
        link = (await db.execute(
            select(BouquetCategory).where(
                BouquetCategory.bouquet_id == bouquet.id,
                BouquetCategory.category_id == cat.id,
            )
        )).scalars().first()
        if not link:
            db.add(BouquetCategory(bouquet_id=bouquet.id, category_id=cat.id, sort_order=0))

    await db.commit()
    logger.info(
        "cdnlive sync: playlists=%d channels=%d countries=%d",
        playlists_upserted, total_channels, len(by_country),
    )
    return {
        "playlists": playlists_upserted,
        "channels": total_channels,
        "countries": len(by_country),
    }


async def cleanup_legacy_cdnlive_streams(db: AsyncSession) -> dict:
    """Undo the first (auto-import) sync: delete every cdnlive Stream it created.

    Stops FFmpeg for each, deletes the Stream (its sources cascade), then drops
    any country category that's left empty — except categories that back the
    user's own premium playlists (United States, Canada, UK Radio), which are
    preserved even when empty.

    Returns {removed_streams, dropped_categories}.
    """
    _KEEP = {"united states", "canada", "uk radio"}

    streams = (await db.execute(
        select(Stream).where(Stream.stream_url.like("%cdnlivetv.tv%"))
    )).scalars().all()

    touched_cats = set()
    removed = 0
    for s in streams:
        if s.category_id:
            touched_cats.add(s.category_id)
        try:
            await ffmpeg_manager.stop_stream(s.id)
        except Exception:
            pass
        await db.delete(s)
        removed += 1
    await db.flush()

    dropped = 0
    for cid in touched_cats:
        cat = (await db.execute(
            select(StreamCategory).where(StreamCategory.id == cid)
        )).scalars().first()
        if not cat or (cat.name or "").strip().lower() in _KEEP:
            continue
        remaining = (await db.execute(
            select(func.count()).select_from(Stream).where(Stream.category_id == cid)
        )).scalar()
        if remaining:
            continue
        await db.execute(delete(BouquetCategory).where(BouquetCategory.category_id == cid))
        await db.delete(cat)
        dropped += 1

    await db.commit()
    logger.info("cdnlive cleanup: removed %d streams, dropped %d empty categories", removed, dropped)
    return {"removed_streams": removed, "dropped_categories": dropped}


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
        async with AsyncSessionLocal() as db:
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

            # Full catalog refresh — rebuilds the per-country playlist snapshots
            # (new channels, fresh logos). Never touches imported streams.
            if (now - last_full_sync).total_seconds() >= full_sync_interval:
                logger.info("cdnlive: refreshing catalog playlists")
                async with AsyncSessionLocal() as db:
                    await sync_cdnlive(db)
                last_full_sync = now

            # Token refresh
            if (now - last_token_refresh).total_seconds() >= token_refresh_interval:
                await _refresh_expiring_tokens()
                last_token_refresh = now

        except Exception as e:
            logger.error("cdnlive loop error: %s", e)

        await asyncio.sleep(60)  # check every minute
