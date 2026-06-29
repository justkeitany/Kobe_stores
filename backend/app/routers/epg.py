import asyncio
import difflib
import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional
import aiohttp
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy import select, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from lxml import etree
from app.auth import get_current_admin
from app.database import get_db, AsyncSessionLocal
from app.models import EpgSource, EpgData, Stream

router = APIRouter(prefix="/api/epg", tags=["epg"])
logger = logging.getLogger(__name__)


class EpgSourceCreate(BaseModel):
    name: str
    url: str
    update_interval_hours: int = 24


class EpgSourceUpdate(BaseModel):
    name: Optional[str] = None
    url: Optional[str] = None
    is_enabled: Optional[bool] = None
    update_interval_hours: Optional[int] = None


class EpgMapping(BaseModel):
    stream_id: int
    epg_channel_id: str


async def fetch_and_parse_epg(source_id: int, url: str):
    """Download XMLTV and parse into epg_data table."""
    logger.info(f"Fetching EPG from {url}")
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    raise Exception(f"HTTP {resp.status}")
                content = await resp.read()

        # Transparently handle gzipped feeds (.xml.gz, or any source serving
        # gzip without a decoded Content-Encoding). XMLTV starts with '<'.
        if content[:2] == b"\x1f\x8b":
            import gzip
            content = gzip.decompress(content)

        root = etree.fromstring(content)
        programmes = root.findall("programme")

        # Channel display-names (e.g. <channel id="Bounce.TV.us2"><display-name>
        # Bounce TV</display-name>) — stored per row so the guide can show a clean
        # name and so auto-mapping can match on the readable name, not the dotted id.
        chan_names: dict[str, str] = {}
        for ch in root.findall("channel"):
            cid = ch.get("id", "")
            dn = ch.find("display-name")
            if cid and dn is not None and dn.text:
                chan_names.setdefault(cid, dn.text.strip())

        async with AsyncSessionLocal() as db:
            # Clear old data for this source
            await db.execute(delete(EpgData).where(EpgData.source_id == source_id))

            batch = []
            for prog in programmes:
                try:
                    start_str = prog.get("start", "")
                    stop_str = prog.get("stop", "")
                    channel = prog.get("channel", "")
                    title_el = prog.find("title")
                    desc_el = prog.find("desc")
                    cat_el = prog.find("category")

                    if not start_str or not stop_str or not channel:
                        continue

                    def parse_xmltv_time(s: str) -> datetime:
                        # Format: 20240101120000 +0000
                        s = s.strip()
                        if " " in s:
                            dt_str, tz_str = s.rsplit(" ", 1)
                        else:
                            dt_str, tz_str = s, "+0000"
                        dt = datetime.strptime(dt_str, "%Y%m%d%H%M%S")
                        sign = 1 if tz_str[0] != "-" else -1
                        tz_str = tz_str.lstrip("+-")
                        h, m = int(tz_str[:2]), int(tz_str[2:])
                        from datetime import timedelta
                        offset = timedelta(hours=h, minutes=m) * sign
                        return (dt - offset).replace(tzinfo=timezone.utc)

                    batch.append(EpgData(
                        channel_id=channel,
                        channel_name=chan_names.get(channel),
                        title=title_el.text if title_el is not None else "Unknown",
                        description=desc_el.text if desc_el is not None else None,
                        start_time=parse_xmltv_time(start_str),
                        end_time=parse_xmltv_time(stop_str),
                        category=cat_el.text if cat_el is not None else None,
                        source_id=source_id,
                    ))

                    if len(batch) >= 500:
                        db.add_all(batch)
                        await db.flush()
                        batch = []

                except Exception as e:
                    logger.warning(f"EPG programme parse error: {e}")
                    continue

            if batch:
                db.add_all(batch)

            # Update source last_updated
            src_res = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
            src = src_res.scalar_one_or_none()
            if src:
                src.last_updated = datetime.now(timezone.utc)

            await db.commit()
            logger.info(f"EPG source {source_id} updated with {len(programmes)} entries")

    except Exception as e:
        logger.error(f"EPG fetch failed for source {source_id}: {e}")


async def epg_loop() -> None:
    """Re-fetch each enabled EPG source once its update_interval_hours elapses.

    XMLTV feeds only carry a few days of programmes, so a source must be
    refreshed periodically or the guide goes stale. We tick every 10 minutes
    and refresh any source whose last_updated is missing or older than its
    configured interval.
    """
    TICK = 600  # seconds between due-checks
    while True:
        try:
            now = datetime.now(timezone.utc)
            due: list[tuple[int, str]] = []
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(EpgSource).where(EpgSource.is_enabled == True)  # noqa: E712
                )
                for src in result.scalars().all():
                    interval = timedelta(hours=max(1, src.update_interval_hours or 24))
                    last = src.last_updated
                    if last is not None and last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    if last is None or now - last >= interval:
                        due.append((src.id, src.url))

            for sid, url in due:
                await fetch_and_parse_epg(sid, url)
        except asyncio.CancelledError:
            break
        except Exception as e:  # never let the loop die on a transient error
            logger.error("EPG refresh loop failed: %s", e)
        await asyncio.sleep(TICK)


async def epg_match_loop() -> None:
    """Re-apply EPG channel mappings periodically.

    Stream auto-map + playlist-channel tagging are normally one-shot, but a
    playlist refresh rewrites its cached channels (dropping the `epg` tags) and
    new channels appear over time. Re-running daily keeps every channel's guide
    self-healing without manual intervention. First run shortly after startup.
    """
    await asyncio.sleep(300)
    while True:
        try:
            async with AsyncSessionLocal() as db:
                r = await automap(only_unmapped=True, dry_run=False, db=db, _=None)
                pr = await automap_playlists(db=db, _=None)
                logger.info("EPG re-match: streams +%d, playlist channels %d/%d tagged",
                            r.matched, pr["matched"], pr["playlist_channels"])
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("EPG match loop failed: %s", e)
        await asyncio.sleep(24 * 3600)


@router.get("/sources")
async def list_sources(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).order_by(EpgSource.id))
    return result.scalars().all()


@router.post("/sources", status_code=201)
async def add_source(
    data: EpgSourceCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    src = EpgSource(**data.model_dump())
    db.add(src)
    await db.commit()
    await db.refresh(src)
    # Kick off initial fetch in background
    background_tasks.add_task(fetch_and_parse_epg, src.id, src.url)
    return src


@router.post("/sources/{source_id}/refresh")
async def refresh_source(
    source_id: int,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "EPG source not found")
    background_tasks.add_task(fetch_and_parse_epg, src.id, src.url)
    return {"message": "EPG refresh started"}


@router.put("/sources/{source_id}")
async def update_source(
    source_id: int,
    data: EpgSourceUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "EPG source not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(src, k, v)
    await db.commit()
    await db.refresh(src)
    return src


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(
    source_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(EpgSource).where(EpgSource.id == source_id))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(404, "EPG source not found")
    await db.delete(src)
    await db.commit()


@router.post("/map")
async def map_stream_to_epg(
    data: EpgMapping,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Stream).where(Stream.id == data.stream_id))
    stream = result.scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    stream.epg_channel_id = data.epg_channel_id
    await db.commit()
    return {"ok": True}


# ── Auto-mapping ────────────────────────────────────────────────────────────
# Feed channel IDs look like "5 Action.uk", "ABC.ca", "Acapulco Shore Pluto TV.us"
# — a display name plus a country suffix, with many near-duplicate variants
# (HD, +1, casing, spacing). We normalise both sides to a comparable key.

_COUNTRY_SUFFIX = re.compile(r"\.[a-z]{2,3}\d*$")  # ".uk", ".us2", ".ca2"
# Country tokens that appear as PREFIXES on our stream names ("US AMC",
# "CA: Animal Planet") but as a suffix on feed ids — stripped so they don't block
# a match. Kept short to avoid eating real words.
_COUNTRY_PREFIX = re.compile(r"^(us|usa|uk|gb|ca|can)\b")
# Quality / provider noise that shouldn't affect channel identity. NOTE we
# deliberately strip "east/eastern" (the default schedule most US streams want,
# and how .ca feeds label the ET feed) and "feed", but NOT pacific/west/central/
# mountain — those are genuinely different timezones and must stay distinct so
# an Eastern stream never collapses onto a Pacific feed. "+1" (timeshift) is
# likewise kept as a token so base channels never match their +1 variant.
_NOISE = re.compile(
    r"\b("
    r"hd|sd|fhd|uhd|4k|\d+p|"
    r"east|eastern|feed|"
    r"pluto\s*tv|pluto|samsung\s*tv\s*plus|samsung|plex|roku|tubi|stirr|xumo|"
    r"channel|network|tv"
    r")\b"
)


def _norm(name: str, drop_noise: bool) -> str:
    """Normalise a channel name/id to a comparison key."""
    s = name.lower()
    s = _COUNTRY_SUFFIX.sub("", s)          # drop ".uk" / ".ca" / ".us2" suffix
    s = s.replace(":", " ")                  # "ca: animal planet" → "ca  animal planet"
    s = _COUNTRY_PREFIX.sub("", s).strip()   # drop leading "us"/"ca" country prefix
    s = s.replace("&", " and ")
    s = re.sub(r"\+\s*1", " plus1 ", s)      # timeshift kept as a token in BOTH forms
    if drop_noise:
        s = _NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)        # strip punctuation
    return " ".join(s.split())


class AutoMapResult(BaseModel):
    total_streams: int
    considered: int
    matched: int
    applied: bool
    samples: list[dict]
    unmatched: list[str]


@router.post("/automap", response_model=AutoMapResult)
async def automap(
    only_unmapped: bool = True,
    dry_run: bool = False,
    cutoff: float = 0.92,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Best-effort link streams to EPG channel IDs by matching names.

    For each stream we try, in order: exact normalised match, exact match with
    quality/provider noise stripped, then a fuzzy match above `cutoff`. When
    several feed IDs collapse to the same key we keep the one with the most
    programmes (the fullest guide). Conservative by design — anything we're not
    confident about is left unmapped so it can be fixed by hand.
    """
    # Programme count per feed channel_id — used to break ties between variants.
    counts = dict((await db.execute(
        select(EpgData.channel_id, func.count()).group_by(EpgData.channel_id)
    )).all())

    # Build normalised indexes, preferring the id with the most programmes.
    full_index: dict[str, str] = {}
    core_index: dict[str, str] = {}

    def _offer(index: dict[str, str], key: str, cid: str):
        if not key:
            return
        cur = index.get(key)
        if cur is None or counts.get(cid, 0) > counts.get(cur, 0):
            index[key] = cid

    # Readable display-name per feed channel_id (populated on ingest). Indexing
    # these as well as the dotted ids makes "US AMC" → "AMC.HD.us2" match via the
    # clean name "AMC HD".
    names = dict((await db.execute(
        select(EpgData.channel_id, EpgData.channel_name)
        .where(EpgData.channel_name.isnot(None)).distinct()
    )).all())

    for cid in counts:
        _offer(full_index, _norm(cid, drop_noise=False), cid)
        _offer(core_index, _norm(cid, drop_noise=True), cid)
        nm = names.get(cid)
        if nm:
            _offer(full_index, _norm(nm, drop_noise=False), cid)
            _offer(core_index, _norm(nm, drop_noise=True), cid)

    full_keys = list(full_index.keys())

    # Candidate streams.
    q = select(Stream)
    if only_unmapped:
        q = q.where((Stream.epg_channel_id.is_(None)) | (Stream.epg_channel_id == ""))
    streams = (await db.execute(q)).scalars().all()

    matched = 0
    samples: list[dict] = []
    unmatched: list[str] = []

    for s in streams:
        nf = _norm(s.name, drop_noise=False)
        nc = _norm(s.name, drop_noise=True)
        cid = None
        method = None

        if nf in full_index:
            cid, method = full_index[nf], "exact"
        elif nc in core_index:
            cid, method = core_index[nc], "core"
        else:
            # Fuzzy, but only accept a candidate whose numbers match exactly —
            # otherwise "ITV+1"~"ITV4 +1" or "Fox Sports"~"Fox Sports 2" sneak
            # through. "Film 4"~"Film4" still passes (same number set).
            want = set(re.findall(r"\d+", nf))
            for cand in difflib.get_close_matches(nf, full_keys, n=5, cutoff=cutoff):
                if set(re.findall(r"\d+", cand)) == want:
                    cid, method = full_index[cand], "fuzzy"
                    break

        if cid is None:
            unmatched.append(s.name)
            continue

        matched += 1
        if not dry_run:
            s.epg_channel_id = cid
        if len(samples) < 60:
            samples.append({
                "stream": s.name,
                "epg_channel_id": cid,
                "method": method,
                "programmes": counts.get(cid, 0),
            })

    if not dry_run and matched:
        await db.commit()

    return AutoMapResult(
        total_streams=len(streams) if only_unmapped else len(streams),
        considered=len(streams),
        matched=matched,
        applied=(not dry_run),
        samples=samples,
        unmatched=unmatched[:200],
    )


async def _build_match_index(db: AsyncSession) -> tuple[dict, dict]:
    """Normalised name → epg channel_id indexes (full + noise-stripped), preferring
    the id with the most programmes. Shared by stream automap and playlist tagging."""
    counts = dict((await db.execute(
        select(EpgData.channel_id, func.count()).group_by(EpgData.channel_id)
    )).all())
    names = dict((await db.execute(
        select(EpgData.channel_id, EpgData.channel_name)
        .where(EpgData.channel_name.isnot(None)).distinct()
    )).all())
    full_index: dict[str, str] = {}
    core_index: dict[str, str] = {}

    def offer(idx: dict, key: str, cid: str):
        if not key:
            return
        cur = idx.get(key)
        if cur is None or counts.get(cid, 0) > counts.get(cur, 0):
            idx[key] = cid

    for cid in counts:
        offer(full_index, _norm(cid, False), cid)
        offer(core_index, _norm(cid, True), cid)
        nm = names.get(cid)
        if nm:
            offer(full_index, _norm(nm, False), cid)
            offer(core_index, _norm(nm, True), cid)
    return full_index, core_index


def _match_name(name: str, full_index: dict, core_index: dict) -> Optional[str]:
    """Exact/core normalised match (no fuzzy — fast for 10k+ channels)."""
    nf = _norm(name, False)
    if nf in full_index:
        return full_index[nf]
    return core_index.get(_norm(name, True))


@router.post("/automap-playlists")
async def automap_playlists(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Tag every PLAYLIST channel (cached, not imported as a stream) with an `epg`
    id by name-matching to the ingested EPG. These channels are served to players
    directly by get.php, so this is what gives the 12k+ playlist channels a guide.
    Stores the id in the cached channel JSON; emitted as tvg-id by get.php/xmltv."""
    from sqlalchemy.orm.attributes import flag_modified
    from app.models import Playlist

    full_index, core_index = await _build_match_index(db)
    playlists = (await db.execute(select(Playlist))).scalars().all()
    total = matched = 0
    for pl in playlists:
        chs = pl.channels or []
        changed = False
        for c in chs:
            total += 1
            cid = _match_name(c.get("name", ""), full_index, core_index)
            if cid:
                matched += 1
                if c.get("epg") != cid:
                    c["epg"] = cid
                    changed = True
        if changed:
            flag_modified(pl, "channels")
    await db.commit()
    return {"playlist_channels": total, "matched": matched,
            "coverage_pct": round(100 * matched / total, 1) if total else 0}


@router.get("/guide")
async def guide(
    category_id: Optional[int] = None,
    hours: int = 4,
    start: Optional[int] = None,
    limit: int = 150,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    """Grid-style guide: channels (streams with EPG) + their programmes within a
    time window. Powers the TV-guide page. `start` is a unix timestamp (defaults
    to the current half-hour); `hours` is the window width."""
    now = datetime.now(timezone.utc)
    if start:
        win_start = datetime.fromtimestamp(start, tz=timezone.utc)
    else:
        win_start = now.replace(minute=0 if now.minute < 30 else 30, second=0, microsecond=0)
    win_end = win_start + timedelta(hours=max(1, min(12, hours)))

    q = select(Stream).where(Stream.epg_channel_id.isnot(None), Stream.epg_channel_id != "")
    if category_id:
        q = q.where(Stream.category_id == category_id)
    q = q.order_by(Stream.name).limit(max(1, min(400, limit)))
    streams = (await db.execute(q)).scalars().all()
    if not streams:
        return {"start": win_start.isoformat(), "end": win_end.isoformat(),
                "now": now.isoformat(), "channels": []}

    cids = list({s.epg_channel_id for s in streams})
    rows = (await db.execute(
        select(EpgData).where(
            EpgData.channel_id.in_(cids),
            EpgData.end_time > win_start,
            EpgData.start_time < win_end,
        ).order_by(EpgData.start_time)
    )).scalars().all()

    by_cid: dict[str, list] = {}
    for r in rows:
        by_cid.setdefault(r.channel_id, []).append({
            "title": r.title,
            "start": r.start_time.isoformat(),
            "stop": r.end_time.isoformat(),
            "desc": r.description,
            "category": r.category,
        })

    channels = []
    for s in streams:
        progs = by_cid.get(s.epg_channel_id)
        if not progs:
            continue  # nothing airing in this window → omit the row
        channels.append({
            "id": s.id,
            "name": s.name,
            "logo": s.logo_url or "",
            "epg_channel_id": s.epg_channel_id,
            "programmes": progs,
        })
    return {"start": win_start.isoformat(), "end": win_end.isoformat(),
            "now": now.isoformat(), "channels": channels}


@router.get("/now/{channel_id}")
async def get_now_playing(channel_id: str, db: AsyncSession = Depends(get_db)):
    """Get current EPG programme for a channel (public endpoint for players)."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(EpgData)
        .where(
            EpgData.channel_id == channel_id,
            EpgData.start_time <= now,
            EpgData.end_time >= now,
        )
        .limit(1)
    )
    prog = result.scalar_one_or_none()
    if not prog:
        return {}
    return {
        "title": prog.title,
        "description": prog.description,
        "start": prog.start_time,
        "end": prog.end_time,
        "category": prog.category,
    }


@router.get("/timeline/{stream_id}")
async def channel_timeline(stream_id: int, db: AsyncSession = Depends(get_db)):
    """Public per-channel EPG strip for the in-player overlay.

    Returns the programmes airing on this stream's mapped EPG channel in a window
    around 'now' (a few hours of history so the user can scroll back, ~24h ahead
    for upcoming). Empty programmes list when the stream has no EPG mapping or no
    data — the player then just shows a "no guide" message. Public (no admin) so
    shared player links work, mirroring /now/{channel_id}.
    """
    now = datetime.now(timezone.utc)
    stream = (await db.execute(
        select(Stream).where(Stream.id == stream_id)
    )).scalar_one_or_none()
    if not stream or not stream.epg_channel_id:
        return {"now": now.isoformat(),
                "channel_name": stream.name if stream else "",
                "epg_channel_id": "", "programmes": []}

    win_start = now - timedelta(hours=4)
    win_end = now + timedelta(hours=24)
    rows = (await db.execute(
        select(EpgData).where(
            EpgData.channel_id == stream.epg_channel_id,
            EpgData.end_time > win_start,
            EpgData.start_time < win_end,
        ).order_by(EpgData.start_time)
    )).scalars().all()
    return {
        "now": now.isoformat(),
        "channel_name": stream.name,
        "epg_channel_id": stream.epg_channel_id,
        "programmes": [{
            "title": r.title,
            "start": r.start_time.isoformat(),
            "stop": r.end_time.isoformat(),
            "desc": r.description,
            "category": r.category,
        } for r in rows],
    }
