"""Live-viewer tracking for accurate dashboard figures.

Every restream viewer — whether on the HLS (.m3u8 polling) path or the .ts
progressive path — is recorded in a single Redis sorted set, scored by last-seen
time. This lets the dashboard report true concurrent figures regardless of
delivery path (the old tiles either counted enabled *accounts* or only saw HLS
streams registered in ffmpeg_manager, so .ts viewers were invisible).

Entries decay after VIEWER_WINDOW seconds without a refresh. HLS players refresh
naturally (they poll the playlist every ~2s); the long-lived .ts generator
refreshes periodically and removes its entry the moment the client disconnects.

Tracking is unconditional — independent of the per-user connection limit — so
admin/unlimited accounts (which skip the limit check) are still counted.
"""
import logging
from datetime import datetime, timezone

from app.redis_client import get_redis

logger = logging.getLogger(__name__)

_KEY = "viewers:live"
_SEP = "\x1f"  # unit separator — safe delimiter, never appears in our fields
VIEWER_WINDOW = 45  # seconds an entry stays "live" without a refresh


def _member(username: str, client_key: str, stream_id: int) -> str:
    return f"{username}{_SEP}{client_key}{_SEP}{stream_id}"


async def track_viewer(username: str, client_key: str, stream_id: int) -> None:
    """Record/refresh one live viewer (user + client + stream)."""
    try:
        redis = await get_redis()
        now = datetime.now(timezone.utc).timestamp()
        await redis.zadd(_KEY, {_member(username, client_key, stream_id): now})
        await redis.zremrangebyscore(_KEY, 0, now - VIEWER_WINDOW)
        await redis.expire(_KEY, VIEWER_WINDOW * 4)
    except Exception as e:  # tracking must never break playback
        logger.debug("track_viewer failed: %s", e)


async def untrack_viewer(username: str, client_key: str, stream_id: int) -> None:
    """Drop a viewer immediately (e.g. .ts disconnect) so counts decay fast."""
    try:
        redis = await get_redis()
        await redis.zrem(_KEY, _member(username, client_key, stream_id))
    except Exception:
        pass


async def live_counts() -> dict:
    """Current concurrent figures derived from the live-viewer set.

    - active_connections: distinct (user, client) sessions currently watching
      (mirrors the per-user connection-limit semantics, which key on client_key).
    - active_streams: distinct streams with at least one live viewer.
    Both span HLS and .ts delivery.
    """
    try:
        redis = await get_redis()
        now = datetime.now(timezone.utc).timestamp()
        await redis.zremrangebyscore(_KEY, 0, now - VIEWER_WINDOW)
        members = await redis.zrange(_KEY, 0, -1)
    except Exception:
        return {"active_connections": 0, "active_streams": 0}

    conns, streams = set(), set()
    for m in members:
        if isinstance(m, (bytes, bytearray)):
            m = m.decode(errors="replace")
        parts = m.split(_SEP)
        if len(parts) != 3:
            continue
        user, client, sid = parts
        conns.add((user, client))
        streams.add(sid)
    return {"active_connections": len(conns), "active_streams": len(streams)}
