"""
Full Xtream Codes API implementation (no PHP).
All endpoints served by FastAPI.

Supported endpoints:
  GET/POST  /player_api.php          — authentication + data actions
  GET       /get.php                 — M3U playlist download
  GET       /live/{user}/{pass}/{id} — stream redirect to HLS
  GET       /xmltv.php               — XMLTV EPG data
"""
import os
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Request, Response, HTTPException, Query
from fastapi.responses import StreamingResponse, RedirectResponse, PlainTextResponse
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Stream, StreamCategory, EpgData, Settings as SettingsModel
from app.ffmpeg_manager import ffmpeg_manager
from app.config import settings

router = APIRouter(tags=["xtream"])
logger = logging.getLogger(__name__)


async def _configured_server_url() -> Optional[str]:
    """The Public Server URL set in the dashboard (DB Settings), or None."""
    async with AsyncSessionLocal() as db:
        row = await db.execute(
            select(SettingsModel).where(SettingsModel.key == "server_url")
        )
        setting = row.scalar_one_or_none()
    if setting and setting.value and setting.value.strip():
        return setting.value.strip()
    return None


def _base_from_request(request: Request) -> str:
    """Derive the public base URL from the incoming request (honours proxy headers)."""
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = (
        request.headers.get("x-forwarded-host")
        or request.headers.get("host")
        or request.url.netloc
    )
    return f"{proto}://{host}"


async def _base_url(request: Request) -> str:
    """Public base URL: the dashboard-configured domain if set, else the request host:port."""
    configured = await _configured_server_url()
    if configured:
        return configured.rstrip("/")
    return _base_from_request(request).rstrip("/")


async def _check_credentials(username: str, password: str):
    """
    Check credentials against:
    1. Admin credentials (settings)
    2. IPTV users table
    Returns user info dict if valid, None if invalid.
    """
    from app.routers.users import IPTVUser
    from datetime import timezone as tz

    # Check admin credentials first
    if username == settings.ADMIN_USERNAME and password == settings.ADMIN_PASSWORD:
        return {
            "username": username,
            "password": password,
            "auth": 1,
            "status": "Active",
            "exp_date": None,
            "max_connections": "999",
            "is_trial": "0",
        }

    # Check IPTV users table
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(IPTVUser).where(
                IPTVUser.username == username,
                IPTVUser.password == password,
                IPTVUser.is_active == True,
            )
        )
        user = result.scalar_one_or_none()

    if not user:
        return None

    # Check expiry
    if user.expires_at and user.expires_at.replace(tzinfo=tz.utc) < datetime.now(tz.utc):
        return None

    exp_str = user.expires_at.strftime("%Y-%m-%d") if user.expires_at else None
    exp_ts = str(int(user.expires_at.timestamp())) if user.expires_at else None
    return {
        "username": user.username,
        "password": user.password,
        "auth": 1,
        "status": "Active",
        "exp_date": exp_ts,
        "max_connections": str(user.max_connections),
        "is_trial": "0",
    }


def _server_info(base_url: str) -> dict:
    from urllib.parse import urlparse

    parsed = urlparse(base_url)
    protocol = parsed.scheme or "http"
    host = parsed.hostname or base_url
    if parsed.port:
        port = str(parsed.port)
    else:
        port = "443" if protocol == "https" else "80"

    return {
        "url": host,
        "port": port,
        "https_port": "443",
        "server_protocol": protocol,
        "rtmp_port": "1935",
        "timezone": "UTC",
        "timestamp_now": int(datetime.now(timezone.utc).timestamp()),
        "time_now": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _user_info_from_data(data: dict) -> dict:
    return {
        "username": data["username"],
        "password": data["password"],
        "message": "",
        "auth": 1,
        "status": data.get("status", "Active"),
        "exp_date": data.get("exp_date"),
        "is_trial": data.get("is_trial", "0"),
        "active_cons": "0",
        "created_at": "0",
        "max_connections": data.get("max_connections", "1"),
        "allowed_output_formats": ["ts", "m3u8", "rtmp"],
        "is_mag": "0",
        "is_stalker": "0",
        "package": "",
    }


# ── /player_api.php ────────────────────────────────────────────────────────

@router.get("/player_api.php")
@router.post("/player_api.php")
async def player_api(
    request: Request,
    username: Optional[str] = Query(None),
    password: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    category_id: Optional[str] = Query(None),
    vod_id: Optional[str] = Query(None),
    series_id: Optional[str] = Query(None),
    stream_id: Optional[str] = Query(None),
    limit: Optional[str] = Query(None),
    offset: Optional[str] = Query(None),
):
    # Also parse from POST body
    if request.method == "POST":
        form = await request.form()
        username = username or form.get("username")
        password = password or form.get("password")
        action = action or form.get("action")
        category_id = category_id or form.get("category_id")
        stream_id = stream_id or form.get("stream_id")

    user_data = await _check_credentials(username or "", password or "")
    if not user_data:
        return {"user_info": {"auth": 0}}

    async with AsyncSessionLocal() as db:
        # Authentication / handshake
        if not action:
            return {
                "user_info": _user_info_from_data(user_data),
                "server_info": _server_info(await _base_url(request)),
            }

        # ── Live streams ──────────────────────────────────────────────────
        if action == "get_live_categories":
            result = await db.execute(
                select(StreamCategory).order_by(StreamCategory.sort_order)
            )
            cats = result.scalars().all()
            return [
                {
                    "category_id": str(c.id),
                    "category_name": c.name,
                    "parent_id": 0,
                }
                for c in cats
            ]

        if action == "get_live_streams":
            q = select(Stream).where(Stream.is_enabled == True)
            if category_id:
                q = q.where(Stream.category_id == int(category_id))
            q = q.order_by(Stream.sort_order, Stream.id)
            result = await db.execute(q)
            streams = result.scalars().all()

            return [
                {
                    "num": s.id,
                    "name": s.name,
                    "stream_type": "live",
                    "stream_id": s.id,
                    "stream_icon": s.logo_url or "",
                    "epg_channel_id": s.epg_channel_id or "",
                    "added": "0",
                    "category_id": str(s.category_id) if s.category_id else "0",
                    "custom_sid": "",
                    "tv_archive": 0,
                    "direct_source": "",
                    "tv_archive_duration": 0,
                }
                for s in streams
            ]

        # ── EPG ───────────────────────────────────────────────────────────
        if action == "get_short_epg" and stream_id:
            result = await db.execute(
                select(Stream).where(Stream.id == int(stream_id))
            )
            stream = result.scalar_one_or_none()
            if stream and stream.epg_channel_id:
                now = datetime.now(timezone.utc)
                epg_res = await db.execute(
                    select(EpgData)
                    .where(
                        EpgData.channel_id == stream.epg_channel_id,
                        EpgData.end_time >= now,
                    )
                    .order_by(EpgData.start_time)
                    .limit(int(limit or 4))
                )
                programmes = epg_res.scalars().all()
                return {
                    "epg_listings": [
                        {
                            "id": p.id,
                            "epg_id": p.channel_id,
                            "title": p.title,
                            "lang": "",
                            "start": p.start_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "end": p.end_time.strftime("%Y-%m-%d %H:%M:%S"),
                            "description": p.description or "",
                            "channel_id": p.channel_id,
                            "start_timestamp": int(p.start_time.timestamp()),
                            "stop_timestamp": int(p.end_time.timestamp()),
                        }
                        for p in programmes
                    ]
                }
            return {"epg_listings": []}

        if action == "get_simple_data_table" and stream_id:
            return await player_api(
                request=request,
                username=username,
                password=password,
                action="get_short_epg",
                stream_id=stream_id,
                limit=limit,
            )

    return {}


# ── /get.php — M3U playlist download ──────────────────────────────────────

@router.get("/get.php")
async def get_playlist(
    request: Request,
    username: str = Query(...),
    password: str = Query(...),
    type: str = Query("m3u_plus"),
    output: str = Query("ts"),
):
    user_data = await _check_credentials(username, password)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    base_url = await _base_url(request)

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Stream, StreamCategory)
            .outerjoin(StreamCategory, Stream.category_id == StreamCategory.id)
            .where(Stream.is_enabled == True)
            .order_by(StreamCategory.sort_order, Stream.sort_order, Stream.id)
        )
        rows = result.all()

    lines = ["#EXTM3U"]
    for stream, cat in rows:
        cat_name = cat.name if cat else "Uncategorized"
        stream_url = f"{base_url}/live/{username}/{password}/{stream.id}"
        if output == "ts":
            stream_url += ".ts"
        elif output == "m3u8":
            stream_url += ".m3u8"

        lines.append(
            f'#EXTINF:-1 tvg-id="{stream.epg_channel_id or ""}" '
            f'tvg-name="{stream.name}" '
            f'tvg-logo="{stream.logo_url or ""}" '
            f'group-title="{cat_name}",{stream.name}'
        )
        lines.append(stream_url)

    content = "\n".join(lines)
    return Response(
        content=content,
        media_type="audio/x-mpegurl",
        headers={"Content-Disposition": f'attachment; filename="playlist.m3u"'},
    )


# ── /live/{user}/{pass}/{id} — stream delivery ────────────────────────────

def _client_key(request: Request) -> str:
    """Stable per-viewer key used to count concurrent viewers."""
    return (
        request.headers.get("x-real-ip")
        or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        or (request.client.host if request.client else "unknown")
    )


def _rewrite_playlist(path: str, stream_id: int) -> str:
    """Read FFmpeg's HLS playlist and point segment lines at Nginx (/hls/<id>/)."""
    with open(path) as f:
        lines = f.read().splitlines()
    out = []
    for line in lines:
        if line and not line.startswith("#"):
            out.append(f"/hls/{stream_id}/{line.split('/')[-1]}")
        else:
            out.append(line)
    return "\n".join(out) + "\n"


@router.get("/live/{username}/{password}/{stream_file}")
async def serve_live(username: str, password: str, stream_file: str, request: Request):
    user_data = await _check_credentials(username, password)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    # Extract stream ID from filename (e.g. "123.ts" or "123.m3u8" or "123")
    stream_id_str = stream_file.split(".")[0]
    try:
        stream_id = int(stream_id_str)
    except ValueError:
        raise HTTPException(400, "Invalid stream ID")
    ext = stream_file.rsplit(".", 1)[-1].lower() if "." in stream_file else ""

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Stream).where(Stream.id == stream_id))
        stream = result.scalar_one_or_none()

    if not stream or not stream.is_enabled:
        raise HTTPException(404, "Stream not found")

    # Start FFmpeg if needed and record this viewer's heartbeat. The player keeps
    # polling the .m3u8 below (~every 2s), so each poll refreshes the heartbeat;
    # when it stops polling, the manager's reaper stops FFmpeg within ~8s.
    sp = await ffmpeg_manager.start_stream(
        stream_id, stream.stream_url, stream.name, _client_key(request)
    )

    if ext == "m3u8":
        # Serve the live playlist from the backend so every poll is a heartbeat.
        playlist_path = sp.hls_playlist
        for _ in range(20):
            if os.path.exists(playlist_path):
                break
            await asyncio.sleep(0.5)
        try:
            content = _rewrite_playlist(playlist_path, stream_id)
        except FileNotFoundError:
            raise HTTPException(503, "Stream is starting, please retry")
        return Response(
            content=content,
            media_type="application/vnd.apple.mpegurl",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )

    # .ts (or extensionless) — send the player to the heartbeat-tracked playlist.
    # Relative redirect keeps the player on the same host:port it connected to.
    return RedirectResponse(
        url=f"/live/{username}/{password}/{stream_id}.m3u8", status_code=302
    )


# ── /xmltv.php — XMLTV EPG output ─────────────────────────────────────────

@router.get("/xmltv.php")
async def xmltv(
    username: str = Query(...),
    password: str = Query(...),
):
    user_data = await _check_credentials(username, password)
    if not user_data:
        raise HTTPException(401, "Unauthorized")

    async with AsyncSessionLocal() as db:
        streams_res = await db.execute(
            select(Stream).where(Stream.epg_channel_id != None, Stream.is_enabled == True)
        )
        streams = streams_res.scalars().all()

        now = datetime.now(timezone.utc)
        epg_res = await db.execute(
            select(EpgData)
            .where(EpgData.end_time >= now)
            .order_by(EpgData.channel_id, EpgData.start_time)
            .limit(50000)
        )
        programmes = epg_res.scalars().all()

    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '<!DOCTYPE tv SYSTEM "xmltv.dtd">', '<tv>']

    for s in streams:
        lines.append(f'  <channel id="{s.epg_channel_id}">')
        lines.append(f'    <display-name>{_xml_escape(s.name)}</display-name>')
        if s.logo_url:
            lines.append(f'    <icon src="{s.logo_url}" />')
        lines.append("  </channel>")

    for p in programmes:
        start = p.start_time.strftime("%Y%m%d%H%M%S +0000")
        stop = p.end_time.strftime("%Y%m%d%H%M%S +0000")
        lines.append(f'  <programme start="{start}" stop="{stop}" channel="{p.channel_id}">')
        lines.append(f'    <title lang="en">{_xml_escape(p.title)}</title>')
        if p.description:
            lines.append(f'    <desc lang="en">{_xml_escape(p.description)}</desc>')
        if p.category:
            lines.append(f'    <category lang="en">{_xml_escape(p.category)}</category>')
        lines.append("  </programme>")

    lines.append("</tv>")
    return PlainTextResponse("\n".join(lines), media_type="application/xml")


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
    )
