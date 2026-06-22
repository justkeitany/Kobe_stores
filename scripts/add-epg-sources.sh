#!/bin/bash
# ============================================================
# IPTV Panel — Add EPG Sources (USA / UK / Canada)
# Run on the VPS: sudo bash scripts/add-epg-sources.sh
#
# Inserts the Globe TV + Freeview XMLTV feeds as EPG sources
# (idempotent — skips any URL already present) and fetches each
# one once so guide data is available immediately.
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'

APP_DIR="/opt/iptv-panel"
PY="$APP_DIR/venv/bin/python"

[[ ! -x "$PY" ]] && echo -e "${RED}Python venv not found at $PY${NC}" && exit 1
[[ ! -d "$APP_DIR/backend" ]] && echo -e "${RED}Backend not found at $APP_DIR/backend${NC}" && exit 1

echo -e "${CYAN}"
echo "================================================"
echo "   IPTV Panel — Adding EPG Sources"
echo "================================================"
echo -e "${NC}"

# Run from backend/ so app.config loads .env (DATABASE_URL) correctly.
cd "$APP_DIR/backend"

"$PY" - <<'PYEOF'
import asyncio
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import EpgSource
from app.routers.epg import fetch_and_parse_epg

BASE = "https://raw.githubusercontent.com/globetvapp/epg/main"

SOURCES = [
    # United States
    ("Globe TV — USA 1", f"{BASE}/Usa/usa1.xml"),
    ("Globe TV — USA 2", f"{BASE}/Usa/usa2.xml"),
    ("Globe TV — USA 3", f"{BASE}/Usa/usa3.xml"),
    ("Globe TV — USA 4", f"{BASE}/Usa/usa4.xml"),
    ("Globe TV — USA 5", f"{BASE}/Usa/usa5.xml"),
    ("Globe TV — USA 6", f"{BASE}/Usa/usa6.xml"),
    # United Kingdom
    ("Globe TV — UK 1", f"{BASE}/Unitedkingdom/unitedkingdom1.xml"),
    ("Globe TV — UK 2", f"{BASE}/Unitedkingdom/unitedkingdom2.xml"),
    ("Globe TV — UK 3", f"{BASE}/Unitedkingdom/unitedkingdom3.xml"),
    ("Globe TV — UK 4", f"{BASE}/Unitedkingdom/unitedkingdom4.xml"),
    ("Globe TV — UK 5", f"{BASE}/Unitedkingdom/unitedkingdom5.xml"),
    ("Freeview EPG — UK", "https://raw.githubusercontent.com/dp247/Freeview-EPG/master/epg.xml"),
    # Canada
    ("Globe TV — Canada 1", f"{BASE}/Canada/canada1.xml"),
    ("Globe TV — Canada 2", f"{BASE}/Canada/canada2.xml"),
    ("Globe TV — Canada 3", f"{BASE}/Canada/canada3.xml"),
]


async def main():
    # Snapshot existing URLs so re-runs don't create duplicates.
    async with AsyncSessionLocal() as db:
        existing = set((await db.execute(select(EpgSource.url))).scalars().all())

    to_fetch = []
    async with AsyncSessionLocal() as db:
        for name, url in SOURCES:
            if url in existing:
                print(f"  = skip (already added): {name}")
                continue
            src = EpgSource(name=name, url=url, update_interval_hours=24)
            db.add(src)
            await db.commit()
            await db.refresh(src)
            to_fetch.append((src.id, name, url))
            print(f"  + added: {name}")

    if not to_fetch:
        print("\nNothing new to add. All sources already present.")
        return

    print(f"\nFetching guide data for {len(to_fetch)} new source(s)...")
    for sid, name, url in to_fetch:
        print(f"  ... {name}")
        await fetch_and_parse_epg(sid, url)

    print("\nDone. Map your channels to EPG channel IDs on the EPG page.")


asyncio.run(main())
PYEOF

echo -e "${GREEN}"
echo "================================================"
echo "   EPG sources added."
echo "================================================"
echo -e "${NC}"
echo -e "${YELLOW}Next: open the EPG page in the dashboard and map each"
echo -e "channel's epg_channel_id to the IDs inside these feeds.${NC}"
