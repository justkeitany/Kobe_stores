"""Proxy-assisted M3U8 resolution.

Some upstreams geo-gate at the *playlist* level: the initial M3U8 request must
come from an in-region IP, but the segment CDN it points to does not re-check.
For streams with a ``proxy_country`` set we exploit that — the playlist URL is
fetched THROUGH a residential proxy in that country (geo-bypass), and only the
resolved (post-redirect) playlist URL is handed to FFmpeg, WITHOUT a proxy. The
heavy segment traffic then flows direct, so a metered proxy only ever pays for
the tiny playlist body.

  • The resolved URL is cached 30 min per stream, so the proxy is hit at most
    twice an hour per channel regardless of viewer count.
  • If segments turn out to be geo-gated too (FFmpeg reports HTTP 403 mid-
    stream), ``trip_fallback`` flips that one stream to full proxy routing for
    an hour: every FFmpeg request (playlist + segments) goes via the proxy.
  • Proxy bandwidth is metered in Redis and surfaced on the Server page.

Proxy credentials live in the DB ``settings`` table, never in the repo.
"""
import json
import logging
from typing import Optional

import httpx

from app.database import AsyncSessionLocal
from app.models import Settings as SettingsModel
from app.redis_client import get_redis
from app.pluto_stream import resolve as resolve_pluto_url
from sqlalchemy import select

logger = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

RESOLVE_TTL = 1800          # 30 min — cached resolved playlist URL per stream
FALLBACK_TTL = 3600         # 1 h — full-proxy routing after a 403 on segments
DEFAULT_QUOTA = 1024 ** 3   # 1 GB default proxy-bandwidth budget

# settings keys
K_POOL = "proxy_pool"        # raw user input: one "host:port:user:pass" per line
K_MAP = "proxy_map"          # JSON list[{host,port,user,pass,country}] (geo-tagged)
K_QUOTA = "proxy_quota_bytes"

# redis keys
_BW_KEY = "proxy_bw_bytes"
_RR_KEY = "proxy_rr"                       # round-robin cursor per country: proxy_rr:<cc>
def _resolved_key(sid: int) -> str: return f"resolved:{sid}"
def _fallback_key(sid: int) -> str: return f"proxyfb:{sid}"


# ── settings helpers ────────────────────────────────────────────────────────

async def get_setting(key: str) -> Optional[str]:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(SettingsModel).where(SettingsModel.key == key)
        )).scalar_one_or_none()
        return row.value if row else None


async def set_setting(key: str, value: str) -> None:
    async with AsyncSessionLocal() as db:
        row = (await db.execute(
            select(SettingsModel).where(SettingsModel.key == key)
        )).scalar_one_or_none()
        if row:
            row.value = value
        else:
            db.add(SettingsModel(key=key, value=value))
        await db.commit()


def parse_pool(raw: str) -> list[dict]:
    """Parse "host:port:user:pass" lines into proxy dicts (user:pass optional)."""
    out: list[dict] = []
    for line in (raw or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(":")
        if len(parts) < 2:
            continue
        host, port = parts[0], parts[1]
        user = parts[2] if len(parts) > 2 else ""
        pwd = parts[3] if len(parts) > 3 else ""
        out.append({"host": host, "port": port, "user": user, "pass": pwd})
    return out


def proxy_url(p: dict) -> str:
    if p.get("user"):
        return f"http://{p['user']}:{p['pass']}@{p['host']}:{p['port']}"
    return f"http://{p['host']}:{p['port']}"


def _client(proxy: Optional[str], timeout: float = 20.0) -> httpx.AsyncClient:
    """httpx client that works across versions (proxy= vs proxies=)."""
    kw = dict(follow_redirects=True, timeout=timeout, headers={"User-Agent": _UA})
    if not proxy:
        return httpx.AsyncClient(**kw)
    try:
        return httpx.AsyncClient(proxy=proxy, **kw)          # httpx >= 0.26
    except TypeError:
        return httpx.AsyncClient(proxies=proxy, **kw)        # older httpx


# ── proxy selection ─────────────────────────────────────────────────────────

async def _map() -> list[dict]:
    raw = await get_setting(K_MAP)
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return []


async def pick_proxy(country: Optional[str]) -> Optional[str]:
    """Return a proxy URL for the given ISO country code, round-robining across
    all proxies that geo-located to that country."""
    if not country:
        return None
    cc = country.strip().upper()
    cands = [p for p in await _map() if (p.get("country") or "").upper() == cc]
    if not cands:
        return None
    try:
        r = await get_redis()
        idx = await r.incr(f"{_RR_KEY}:{cc}")
    except Exception:
        idx = 0
    return proxy_url(cands[(idx - 1) % len(cands)])


async def detect_country(p: dict) -> str:
    """Geo-locate a proxy by its egress IP (ipinfo) — used when saving the pool."""
    try:
        async with _client(proxy_url(p), timeout=15) as c:
            r = await c.get("https://ipinfo.io/json")
            return (r.json().get("country") or "").upper()
    except Exception as e:
        logger.warning("proxy geo-detect failed for %s:%s — %s", p.get("host"), p.get("port"), e)
        return ""


# ── bandwidth metering ──────────────────────────────────────────────────────

async def _bw_add(n: int) -> None:
    try:
        r = await get_redis()
        await r.incrby(_BW_KEY, max(0, int(n)))
    except Exception:
        pass


async def bw_used() -> int:
    try:
        r = await get_redis()
        return int(await r.get(_BW_KEY) or 0)
    except Exception:
        return 0


async def bw_quota() -> int:
    v = await get_setting(K_QUOTA)
    try:
        return int(v) if v else DEFAULT_QUOTA
    except (ValueError, TypeError):
        return DEFAULT_QUOTA


async def bw_reset() -> None:
    try:
        r = await get_redis()
        await r.delete(_BW_KEY)
    except Exception:
        pass


# ── resolution ──────────────────────────────────────────────────────────────

async def resolve_input(url: str, stream_id: Optional[int] = None,
                        proxy_country: Optional[str] = None) -> str:
    """Return the URL FFmpeg should open as its input.

    Applies Pluto rewriting always. For a proxy_country stream NOT in 403
    fallback, fetches the playlist through the country proxy and returns the
    resolved (post-redirect) URL, cached 30 min. Falls back to the plain URL on
    any error so a viewer is never left without a stream.
    """
    url = resolve_pluto_url(url)
    if not stream_id or not proxy_country:
        return url

    r = await get_redis()
    # In full-proxy fallback we DON'T pre-resolve — FFmpeg pulls everything via
    # the proxy itself (see proxy_args), so hand it the original URL.
    if await r.get(_fallback_key(stream_id)):
        return url

    cached = await r.get(_resolved_key(stream_id))
    if cached:
        return cached

    proxy = await pick_proxy(proxy_country)
    if not proxy:
        return url
    try:
        async with _client(proxy) as c:
            resp = await c.get(url)
        await _bw_add(len(resp.content) + 600)  # body + rough header overhead
        if resp.status_code >= 400:
            logger.info("proxy resolve s=%s got HTTP %s — using direct URL",
                        stream_id, resp.status_code)
            return url
        final = str(resp.url)
        await r.setex(_resolved_key(stream_id), RESOLVE_TTL, final)
        return final
    except Exception as e:
        logger.warning("proxy resolve failed s=%s: %s", stream_id, e)
        return url


async def proxy_args(stream_id: Optional[int] = None,
                    proxy_country: Optional[str] = None) -> list[str]:
    """FFmpeg input args for full-proxy routing — non-empty ONLY while a stream
    is in 403 fallback. `-http_proxy` must precede `-i` (it's an input option)."""
    if not stream_id or not proxy_country:
        return []
    try:
        r = await get_redis()
        if not await r.get(_fallback_key(stream_id)):
            return []
    except Exception:
        return []
    proxy = await pick_proxy(proxy_country)
    return ["-http_proxy", proxy] if proxy else []


async def trip_fallback(stream_id: int) -> None:
    """Flip a stream to full-proxy routing for an hour (after a segment 403)."""
    try:
        r = await get_redis()
        await r.setex(_fallback_key(stream_id), FALLBACK_TTL, "1")
        await r.delete(_resolved_key(stream_id))
        logger.warning("stream %s: segments geo-gated → full-proxy fallback (1h)", stream_id)
    except Exception:
        pass
