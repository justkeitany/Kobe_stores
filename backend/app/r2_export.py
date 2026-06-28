"""
Export premium playlists to Cloudflare R2 (S3-compatible) for backup / retrieval.

Each premium playlist (a saved Playlist whose name matches a Premium-bouquet
category) is rendered to a standard extended-M3U from its cached channels — the
same logos + United States / Canada / UK Radio groups shown in the panel — and
uploaded to an R2 bucket. A combined ``premium.m3u`` and a full-fidelity
``premium-snapshot.json`` (exact restore) are uploaded alongside.

Configuration (backend/.env) — all required except the public base:
    R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET
    R2_PREFIX        key prefix, default "playlists/"
    R2_PUBLIC_BASE   optional, e.g. https://backups.example.com — only used to
                     build friendly return URLs; uploads work without it.

Runs daily in the background (when configured) and on demand via
``POST /api/premium/export``. Nothing happens (and nothing errors at startup)
until the R2_* values are set, so this is safe to ship dormant.
"""
import asyncio
import json
import logging
import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Playlist
from app.routers.premium import _premium_categories

logger = logging.getLogger(__name__)

# Daily export; first run shortly after startup so a fresh deploy backs up soon.
_EXPORT_INTERVAL = 24 * 3600
_EXPORT_STARTUP_DELAY = 150


def r2_configured() -> bool:
    """True only when every required R2 credential is present."""
    return all((
        settings.R2_ACCOUNT_ID,
        settings.R2_ACCESS_KEY_ID,
        settings.R2_SECRET_ACCESS_KEY,
        settings.R2_BUCKET,
    ))


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (name or "").strip().lower()).strip("-")
    return s or "playlist"


def _m3u(channels: list[dict]) -> str:
    """Render cached playlist channels to extended-M3U (re-importable)."""
    lines = ["#EXTM3U"]
    for c in channels:
        url = c.get("url")
        if not url:
            continue
        name = (c.get("name") or "Unnamed").replace('"', "")
        logo = (c.get("logo") or "").replace('"', "")
        cat = (c.get("category") or "").replace('"', "")
        cid = c.get("id") or ""
        lines.append(
            f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{name}" '
            f'tvg-logo="{logo}" group-title="{cat}",{name}'
        )
        lines.append(url)
    return "\n".join(lines) + "\n"


async def _premium_playlists(db: AsyncSession) -> list[Playlist]:
    _ids, names = await _premium_categories(db)
    if not names:
        return []
    playlists = (await db.execute(select(Playlist).order_by(Playlist.name))).scalars().all()
    return [p for p in playlists if (p.name or "").strip().lower() in names]


def _r2_client():
    import boto3
    from botocore.config import Config
    return boto3.client(
        "s3",
        endpoint_url=f"https://{settings.R2_ACCOUNT_ID}.r2.cloudflarestorage.com",
        aws_access_key_id=settings.R2_ACCESS_KEY_ID,
        aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


async def export_premium_to_r2() -> dict:
    """Build the premium M3U/JSON files and upload them to R2. Returns a summary."""
    if not r2_configured():
        raise RuntimeError("R2 is not configured (set R2_* in backend/.env)")

    prefix = settings.R2_PREFIX if settings.R2_PREFIX is not None else "playlists/"
    files: dict[str, tuple[str, str]] = {}   # key -> (body, content-type)
    async with AsyncSessionLocal() as db:
        playlists = await _premium_playlists(db)
        combined: list[dict] = []
        snapshot: list[dict] = []
        for p in playlists:
            channels = p.channels or []
            combined.extend(channels)
            files[f"{prefix}{_slug(p.name)}.m3u"] = (_m3u(channels), "audio/x-mpegurl")
            snapshot.append({"name": p.name, "url": p.url, "channels": channels})
    files[f"{prefix}premium.m3u"] = (_m3u(combined), "audio/x-mpegurl")
    files[f"{prefix}premium-snapshot.json"] = (
        json.dumps(snapshot, ensure_ascii=False, indent=2), "application/json"
    )

    def _upload_all() -> list[str]:
        client = _r2_client()
        for key, (body, ctype) in files.items():
            client.put_object(
                Bucket=settings.R2_BUCKET,
                Key=key,
                Body=body.encode("utf-8"),
                ContentType=ctype,
            )
        return list(files.keys())

    # boto3 is sync — run it off the event loop so we don't block the server.
    keys = await asyncio.to_thread(_upload_all)
    base = (settings.R2_PUBLIC_BASE or "").rstrip("/")
    urls = [f"{base}/{k}" for k in keys] if base else []
    logger.info("Exported %d premium files to R2 bucket %s", len(keys), settings.R2_BUCKET)
    return {"bucket": settings.R2_BUCKET, "count": len(keys), "keys": keys, "urls": urls}


async def list_premium_backups(expires: int = 3600) -> list[dict]:
    """List the exported backup objects in R2 with short-lived presigned download
    URLs — so a PRIVATE bucket's files can still be fetched on demand. Returns
    ``[{key, size, last_modified, url}]`` newest-first."""
    if not r2_configured():
        raise RuntimeError("R2 is not configured (set R2_* in backend/.env)")
    prefix = settings.R2_PREFIX if settings.R2_PREFIX is not None else "playlists/"

    def _list() -> list[dict]:
        client = _r2_client()
        resp = client.list_objects_v2(Bucket=settings.R2_BUCKET, Prefix=prefix)
        out = []
        for obj in resp.get("Contents", []):
            url = client.generate_presigned_url(
                "get_object",
                Params={"Bucket": settings.R2_BUCKET, "Key": obj["Key"]},
                ExpiresIn=expires,
            )
            out.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
                "url": url,
            })
        out.sort(key=lambda o: o["last_modified"], reverse=True)
        return out

    return await asyncio.to_thread(_list)


# ── EPG export ───────────────────────────────────────────────────────────────
# The site's channels (streams with an epg_channel_id) plus their programmes,
# rendered to XMLTV and uploaded under EPGs/. The full ~8k-channel catalogue stays
# in the DB as background data; only channels that exist on the site are exported.
_EPG_PREFIX = "EPGs/"


def _xml_escape(text: str) -> str:
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


async def _build_epg_xmltv() -> tuple[str, int, int]:
    """Render XMLTV for every site channel that has an EPG mapping. Returns
    (xml, channel_count, programme_count)."""
    from datetime import datetime, timezone
    from app.models import Stream, EpgData, Playlist

    async with AsyncSessionLocal() as db:
        streams = (await db.execute(
            select(Stream).where(Stream.epg_channel_id.isnot(None), Stream.epg_channel_id != "")
        )).scalars().all()
        cids = {s.epg_channel_id for s in streams if s.epg_channel_id}

        # Playlist channels tagged with an `epg` id (served to players by get.php).
        pl_channels: dict[str, str] = {}
        pls = (await db.execute(select(Playlist).where(Playlist.channels.isnot(None)))).scalars().all()
        for pl in pls:
            for c in (pl.channels or []):
                eid = c.get("epg")
                if eid and eid not in cids:
                    pl_channels.setdefault(eid, c.get("name") or eid)
        cids |= set(pl_channels)

        progs = []
        if cids:
            now = datetime.now(timezone.utc)
            progs = (await db.execute(
                select(EpgData).where(
                    EpgData.channel_id.in_(cids),
                    EpgData.end_time >= now,
                ).order_by(EpgData.channel_id, EpgData.start_time).limit(500000)
            )).scalars().all()

    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
             '<tv generator-info-name="IPTV-Panel">']
    seen = set()
    for s in streams:
        if s.epg_channel_id in seen:
            continue
        seen.add(s.epg_channel_id)
        lines.append(f'  <channel id="{_xml_escape(s.epg_channel_id)}">')
        lines.append(f'    <display-name>{_xml_escape(s.name)}</display-name>')
        if s.logo_url:
            lines.append(f'    <icon src="{_xml_escape(s.logo_url)}" />')
        lines.append('  </channel>')
    for eid, dname in pl_channels.items():
        if eid in seen:
            continue
        seen.add(eid)
        lines.append(f'  <channel id="{_xml_escape(eid)}">')
        lines.append(f'    <display-name>{_xml_escape(dname)}</display-name>')
        lines.append('  </channel>')
    for p in progs:
        start = p.start_time.strftime("%Y%m%d%H%M%S +0000")
        stop = p.end_time.strftime("%Y%m%d%H%M%S +0000")
        lines.append(f'  <programme start="{start}" stop="{stop}" channel="{_xml_escape(p.channel_id)}">')
        lines.append(f'    <title lang="en">{_xml_escape(p.title)}</title>')
        if p.description:
            lines.append(f'    <desc lang="en">{_xml_escape(p.description)}</desc>')
        if p.category:
            lines.append(f'    <category lang="en">{_xml_escape(p.category)}</category>')
        lines.append('  </programme>')
    lines.append('</tv>')
    return "\n".join(lines) + "\n", len(seen), len(progs)


async def export_epg_to_r2() -> dict:
    """Build the site-channel XMLTV and upload it to R2 under EPGs/ (plain + gz)."""
    if not r2_configured():
        raise RuntimeError("R2 is not configured (set R2_* in backend/.env)")
    import gzip

    xml, nchan, nprog = await _build_epg_xmltv()
    raw = xml.encode("utf-8")
    gz = gzip.compress(raw)

    def _upload() -> list[str]:
        client = _r2_client()
        client.put_object(Bucket=settings.R2_BUCKET, Key=f"{_EPG_PREFIX}epg.xml",
                          Body=raw, ContentType="application/xml")
        client.put_object(Bucket=settings.R2_BUCKET, Key=f"{_EPG_PREFIX}epg.xml.gz",
                          Body=gz, ContentType="application/gzip")
        return [f"{_EPG_PREFIX}epg.xml", f"{_EPG_PREFIX}epg.xml.gz"]

    keys = await asyncio.to_thread(_upload)
    logger.info("Exported EPG (%d channels, %d programmes) to R2 %s", nchan, nprog, settings.R2_BUCKET)
    base = (settings.R2_PUBLIC_BASE or "").rstrip("/")
    return {"bucket": settings.R2_BUCKET, "channels": nchan, "programmes": nprog,
            "keys": keys, "urls": [f"{base}/{k}" for k in keys] if base else []}


async def export_full_epg_to_r2() -> dict:
    """Export the ENTIRE epg_data catalogue (all ~10k channels / ~1M programmes) to
    R2 under EPGs/epg-full.xml(.gz). Streamed to a temp file and uploaded from disk
    (boto3 multipart) so the ~200 MB document never sits in memory."""
    if not r2_configured():
        raise RuntimeError("R2 is not configured (set R2_* in backend/.env)")
    import gzip
    import os
    import shutil
    import tempfile
    from sqlalchemy import func
    from app.models import EpgData

    # Distinct channels (best display-name per id).
    async with AsyncSessionLocal() as db:
        chans = (await db.execute(
            select(EpgData.channel_id, func.max(EpgData.channel_name)).group_by(EpgData.channel_id)
        )).all()

    # Unique temp file (avoids collisions between the daily loop and manual runs).
    fd, xml_path = tempfile.mkstemp(prefix="epg-full-", suffix=".xml")
    os.close(fd)
    gz_path = xml_path + ".gz"
    nprog = 0
    with open(xml_path, "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<!DOCTYPE tv SYSTEM "xmltv.dtd">\n')
        f.write('<tv generator-info-name="IPTV-Panel">\n')
        for cid, name in chans:
            f.write(f'  <channel id="{_xml_escape(cid)}"><display-name>'
                    f'{_xml_escape(name or cid)}</display-name></channel>\n')
        # Programmes, paginated by primary key so memory stays flat.
        last_id = 0
        while True:
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(
                    select(EpgData).where(EpgData.id > last_id)
                    .order_by(EpgData.id).limit(5000)
                )).scalars().all()
            if not rows:
                break
            for p in rows:
                last_id = p.id
                start = p.start_time.strftime("%Y%m%d%H%M%S +0000")
                stop = p.end_time.strftime("%Y%m%d%H%M%S +0000")
                f.write(f'  <programme start="{start}" stop="{stop}" channel="{_xml_escape(p.channel_id)}">')
                f.write(f'<title lang="en">{_xml_escape(p.title)}</title>')
                if p.description:
                    f.write(f'<desc lang="en">{_xml_escape(p.description)}</desc>')
                if p.category:
                    f.write(f'<category lang="en">{_xml_escape(p.category)}</category>')
                f.write('</programme>\n')
                nprog += 1
        f.write('</tv>\n')

    with open(xml_path, "rb") as fin, gzip.open(gz_path, "wb") as fout:
        shutil.copyfileobj(fin, fout)

    def _upload() -> None:
        client = _r2_client()
        client.upload_file(xml_path, settings.R2_BUCKET, f"{_EPG_PREFIX}epg-full.xml",
                           ExtraArgs={"ContentType": "application/xml"})
        client.upload_file(gz_path, settings.R2_BUCKET, f"{_EPG_PREFIX}epg-full.xml.gz",
                           ExtraArgs={"ContentType": "application/gzip"})

    await asyncio.to_thread(_upload)
    raw_size, gz_size = os.path.getsize(xml_path), os.path.getsize(gz_path)
    os.remove(xml_path)
    os.remove(gz_path)
    logger.info("Exported FULL EPG (%d channels, %d programmes, %d MB → %d MB gz) to R2 %s",
                len(chans), nprog, raw_size // 1048576, gz_size // 1048576, settings.R2_BUCKET)
    base = (settings.R2_PUBLIC_BASE or "").rstrip("/")
    keys = [f"{_EPG_PREFIX}epg-full.xml", f"{_EPG_PREFIX}epg-full.xml.gz"]
    return {"bucket": settings.R2_BUCKET, "channels": len(chans), "programmes": nprog,
            "raw_bytes": raw_size, "gz_bytes": gz_size, "keys": keys,
            "urls": [f"{base}/{k}" for k in keys] if base else []}


async def r2_export_loop() -> None:
    """Background task: back the premium playlists + EPG up to R2 once a day.

    A no-op while R2 is unconfigured, so it's safe to always start. Never lets a
    transient upload error kill the loop.
    """
    await asyncio.sleep(_EXPORT_STARTUP_DELAY)
    while True:
        try:
            if r2_configured():
                await export_premium_to_r2()
                await export_epg_to_r2()
                await export_full_epg_to_r2()
        except asyncio.CancelledError:
            break
        except Exception as e:  # transient R2/network error — retry next cycle
            logger.error("R2 export failed: %s", e)
        await asyncio.sleep(_EXPORT_INTERVAL)
