"""
Free-streams channel directory passthrough.

GET /api/freestreams/{provider}/channels
    Server-side fetch + parse of a public M3U playlist (BuddyChewChew's
    app-m3u-generator) for a free FAST service — Plex, Samsung TV Plus, Roku or
    Tubi. Proxied through the backend so the browser never has to fetch/parse a
    multi-MB playlist, and to keep the directory shape identical to the Pluto
    passthrough the frontend already consumes.

Unlike Pluto (whose stored stitch URLs must be rewritten to a working resolver),
these playlists already contain directly-playable stream URLs — the jmp2.uk
resolver links (Samsung/Roku) 302-redirect to real content and FFmpeg follows
them, and Plex/Tubi URLs are direct. So the parsed ``url`` is imported as-is and
no resolver step is involved.
"""
import logging
import re

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.auth import get_current_admin

router = APIRouter(prefix="/api/freestreams", tags=["freestreams"])
logger = logging.getLogger(__name__)

# provider key -> display name + source playlist URL.
PROVIDERS: dict[str, dict[str, str]] = {
    "plex": {
        "name": "Plex",
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/plex_us.m3u",
    },
    "samsung": {
        "name": "Samsung TV Plus",
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/samsungtvplus_us.m3u",
    },
    "roku": {
        "name": "Roku",
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/roku_all.m3u",
    },
    "tubi": {
        "name": "Tubi",
        "url": "https://raw.githubusercontent.com/BuddyChewChew/app-m3u-generator/main/playlists/tubi_all.m3u",
    },
}

# Matches  key="value"  attribute pairs inside an #EXTINF line.
_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')


def _parse_m3u(text: str) -> list[dict]:
    """Parse an extended-M3U playlist into normalized channel dicts.

    Each entry is ``#EXTINF:<dur> <attrs>,<display name>`` followed by the
    stream URL on the next non-comment line. Returns dicts shaped like the
    Pluto directory the frontend already understands.
    """
    channels: list[dict] = []
    pending: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#EXTINF"):
            attrs = dict(_ATTR_RE.findall(line))
            display = line.split(",", 1)[1].strip() if "," in line else ""
            pending = {
                "id": attrs.get("channel-id") or attrs.get("tvg-id") or "",
                "name": attrs.get("tvg-name") or display or "Unnamed",
                "category": attrs.get("group-title") or "Uncategorized",
                "logo": attrs.get("tvg-logo") or "",
            }
        elif line.startswith("#"):
            continue
        elif pending is not None:
            pending["url"] = line
            # Fall back to the URL as a stable id when the playlist omits one.
            if not pending["id"]:
                pending["id"] = line
            channels.append(pending)
            pending = None
    return channels


@router.get("/{provider}/channels")
async def list_channels(provider: str, _=Depends(get_current_admin)):
    cfg = PROVIDERS.get(provider)
    if not cfg:
        raise HTTPException(404, "Unknown free-streams provider")
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.get(cfg["url"], headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("%s playlist fetch failed: %s", provider, exc)
        raise HTTPException(502, f"Could not fetch {cfg['name']} channels")
    return {
        "provider": provider,
        "name": cfg["name"],
        "channels": _parse_m3u(resp.text),
    }
