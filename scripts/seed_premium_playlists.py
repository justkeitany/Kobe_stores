#!/usr/bin/env python3
"""
One-off recovery: repopulate a premium playlist's cached channel list from a
local M3U file.

The Premium → Playlists View modal serves each playlist's *own source channels*
from the cached ``Playlist.channels`` snapshot (see
``app.routers.premium._playlist_source_channels``). That cache is normally filled
by the health sweep / Refresh, but if a feed is IP-blocked from the VPS the cache
can be empty and the modal shows nothing — leaving you no list to import from.

Run this once, on the box that hosts the panel, to load the bundled M3U files
into the matching playlists (matched by ``group-title`` → playlist name,
case-insensitive). It only writes the cache columns; it does NOT create streams —
you still press Import in the modal yourself. Safe to re-run (idempotent).

Usage (on the VPS, using the app's virtualenv + .env so DATABASE_URL resolves):

    cd /opt/iptv-panel/backend
    ../venv/bin/python ../scripts/seed_premium_playlists.py \
        ../new.m3u ../uk-radio.m3u

With no file arguments it defaults to new.m3u and uk-radio.m3u next to the repo
root (../ relative to this script).
"""
import asyncio
import os
import sys
from pathlib import Path

# Make ``app`` importable when run from anywhere.
_REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO / "backend"))

from sqlalchemy import select, func  # noqa: E402

from app.database import AsyncSessionLocal  # noqa: E402
from app.models import Playlist  # noqa: E402
from app.routers.playlists import _parse_m3u, _sample_logos  # noqa: E402


def _default_files() -> list[Path]:
    return [_REPO / "new.m3u", _REPO / "uk-radio.m3u"]


def _group_by_category(channels: list[dict]) -> dict[str, list[dict]]:
    """Bucket parsed channels by their group-title (the playlist name)."""
    groups: dict[str, list[dict]] = {}
    for c in channels:
        cat = (c.get("category") or "").strip()
        groups.setdefault(cat, []).append(c)
    return groups


async def _seed_one(db, name: str, channels: list[dict]) -> str:
    p = (await db.execute(
        select(Playlist).where(func.lower(Playlist.name) == name.strip().lower())
    )).scalars().first()
    if not p:
        return f"  ! no playlist named {name!r} — skipped ({len(channels)} channels)"
    p.channels = [
        {
            "id": c.get("id") or "",
            "name": c.get("name") or "Unnamed",
            "logo": c.get("logo") or "",
            "url": c.get("url") or "",
            "category": c.get("category") or p.name,
        }
        for c in channels
        if c.get("url")
    ]
    p.channel_count = len(p.channels)
    p.logos = _sample_logos(p.channels)
    return f"  ✓ {p.name}: cached {p.channel_count} channels"


async def main(paths: list[Path]) -> None:
    async with AsyncSessionLocal() as db:
        for path in paths:
            if not path.exists():
                print(f"  ! {path} not found — skipped")
                continue
            channels = _parse_m3u(path.read_text(encoding="utf-8", errors="replace"))
            for name, group in _group_by_category(channels).items():
                if not name:
                    continue
                print(await _seed_one(db, name, group))
        await db.commit()
    print("Done. Open Premium → Playlists → View and press Import.")


if __name__ == "__main__":
    args = [Path(a) for a in sys.argv[1:]] or _default_files()
    if not os.environ.get("DATABASE_URL") and "DATABASE_URL" not in (
        Path(_REPO / "backend" / ".env").read_text() if (_REPO / "backend" / ".env").exists() else ""
    ):
        print("note: DATABASE_URL not set in env or backend/.env — using the app default.")
    asyncio.run(main(args))
