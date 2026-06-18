"""
YouTube live-stream support.

YouTube live URLs (youtube.com/live/..., /watch?v=..., youtu.be/...) are not
directly ingestible by FFmpeg or playable by IPTV players — the actual media is
a short-lived HLS manifest on *.googlevideo.com that expires every few hours.

This module:
  * detects YouTube URLs,
  * normalises them to a clean canonical form (drops ?si=, ?feature=, … tracking),
  * resolves a fresh HLS manifest URL via `yt-dlp -g -f best`,
  * caches the resolved URL in Redis for 4 hours (keyed per source URL, so any
    number of channels are cached independently),
  * re-resolves automatically when the cached manifest has gone stale (403/404).

The /proxy/stream endpoint (app/routers/proxy.py) and the stream status checker
both go through `proxy_resolve()` so they always hit a fresh manifest rather
than the raw YouTube URL.
"""
import asyncio
import logging
from urllib.parse import urlparse, urlencode, parse_qsl, urlunparse

import httpx

from app.config import settings
from app.redis_client import get_redis

logger = logging.getLogger(__name__)

# Cache resolved manifests for 4 hours.
RESOLVE_CACHE_TTL = 4 * 60 * 60
_CACHE_PREFIX = "ytproxy:"

# Substrings that mark a URL as a YouTube stream we should proxy.
_YOUTUBE_MARKERS = (
    "youtube.com/watch",
    "youtube.com/live",
    "youtube.com/@",
    "youtu.be",
    "googlevideo.com/api/manifest",
)

# Query params that carry real meaning and must survive normalisation.
# Everything else (si, feature, pp, ab_channel, t, …) is tracking noise.
_KEEP_QUERY_KEYS = {"v", "list"}


def is_youtube_url(url: str) -> bool:
    """True if `url` is a YouTube stream we should resolve through the proxy."""
    if not url:
        return False
    u = url.lower()
    return any(marker in u for marker in _YOUTUBE_MARKERS)


def clean_youtube_url(url: str) -> str:
    """
    Strip tracking parameters from a YouTube URL, keeping only meaningful ones.

      https://www.youtube.com/live/asJN9Mi3j1k?si=abc      -> https://www.youtube.com/live/asJN9Mi3j1k
      https://youtu.be/asJN9Mi3j1k?si=abc&feature=share    -> https://youtu.be/asJN9Mi3j1k
      https://www.youtube.com/watch?v=asJN9Mi3j1k&si=abc   -> https://www.youtube.com/watch?v=asJN9Mi3j1k
    """
    if not url:
        return url
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return url.strip()

    kept = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k in _KEEP_QUERY_KEYS]
    query = urlencode(kept)
    # Drop fragments (#...) and trailing slashes on the path.
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse((parsed.scheme, parsed.netloc, path, "", query, ""))


async def _run_ytdlp(url: str) -> str | None:
    """Resolve a fresh direct manifest URL with `yt-dlp -g -f best`."""
    cmd = [settings.YTDLP_PATH, "-g", "-f", "best", "--no-warnings", url]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError:
        logger.error("yt-dlp not found at %s — cannot resolve YouTube streams", settings.YTDLP_PATH)
        return None

    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=45)
    except asyncio.TimeoutError:
        proc.kill()
        logger.warning("yt-dlp timed out resolving %s", url)
        return None

    if proc.returncode != 0:
        logger.warning("yt-dlp failed for %s: %s", url, err.decode(errors="replace")[:300])
        return None

    # `-f best` yields a single combined URL; take the first non-empty line.
    for line in out.decode(errors="replace").splitlines():
        line = line.strip()
        if line:
            return line
    return None


async def resolve_youtube_url(url: str, force: bool = False) -> str | None:
    """
    Return a fresh HLS manifest URL for a YouTube source, using a 4h Redis cache.
    `force=True` bypasses (and refreshes) the cache.
    """
    key = _CACHE_PREFIX + url
    redis = await get_redis()

    if not force:
        try:
            cached = await redis.get(key)
        except Exception as e:  # Redis hiccup — fall through to a live resolve.
            logger.warning("Redis read failed for %s: %s", key, e)
            cached = None
        if cached:
            return cached

    resolved = await _run_ytdlp(url)
    if resolved:
        try:
            await redis.set(key, resolved, ex=RESOLVE_CACHE_TTL)
        except Exception as e:
            logger.warning("Redis write failed for %s: %s", key, e)
    return resolved


async def _is_stale(url: str) -> bool:
    """True if the manifest URL responds 403/404 (expired googlevideo link)."""
    try:
        async with httpx.AsyncClient(timeout=5, follow_redirects=True) as client:
            resp = await client.head(url)
            # Some googlevideo endpoints reject HEAD with 405 — confirm with a
            # tiny ranged GET before deciding the link is dead.
            if resp.status_code == 405:
                resp = await client.get(url, headers={"Range": "bytes=0-0"})
            return resp.status_code in (403, 404)
    except httpx.HTTPError:
        # Network blip — don't treat as stale, keep the cached URL.
        return False


async def proxy_resolve(url: str) -> str | None:
    """
    Resolve a YouTube URL to a currently-valid manifest, re-resolving if the
    cached one has expired (403/404). Shared by the proxy endpoint and the
    stream status checker so both always follow the proxy, never the raw URL.
    """
    resolved = await resolve_youtube_url(url)
    if resolved and await _is_stale(resolved):
        logger.info("Cached YouTube manifest stale for %s — re-resolving", url)
        resolved = await resolve_youtube_url(url, force=True)
    return resolved
