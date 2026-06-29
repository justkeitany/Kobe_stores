"""One-shot full EPG sync.

Refreshes every enabled EPG source immediately (ignoring the per-source update
interval) and then auto-maps every still-unmapped stream + playlist channel to
its EPG channel id. Reuses the app's own functions, so behaviour matches the
periodic epg_loop / epg_match_loop exactly.

Run on the server from the backend working directory, with the venv python:

    cd /opt/iptv-panel/backend
    set -a; . ./.env; set +a
    /opt/iptv-panel/venv/bin/python scripts/sync_epg.py
"""
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import EpgSource
from app.routers.epg import fetch_and_parse_epg, automap, automap_playlists


async def main() -> None:
    async with AsyncSessionLocal() as db:
        srcs = (await db.execute(
            select(EpgSource).where(EpgSource.is_enabled == True)  # noqa: E712
        )).scalars().all()
        sources = [(s.id, s.name, s.url) for s in srcs]

    print(f"Refreshing {len(sources)} enabled EPG sources...", flush=True)
    ok = 0
    for sid, name, url in sources:
        try:
            await fetch_and_parse_epg(sid, url)
            ok += 1
            print(f"  [ok]   {sid} {name}", flush=True)
        except Exception as e:
            print(f"  [FAIL] {sid} {name}: {e}", flush=True)
    print(f"Sources refreshed: {ok}/{len(sources)}", flush=True)

    async with AsyncSessionLocal() as db:
        r = await automap(only_unmapped=True, dry_run=False, db=db, _=None)
        print(
            f"Stream automap: matched {r.matched} of {r.considered} considered "
            f"({r.total_streams} total streams)",
            flush=True,
        )
        pr = await automap_playlists(db=db, _=None)
        matched = pr.get("matched")
        total = pr.get("playlist_channels")
        print(f"Playlist channels tagged: {matched}/{total}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
