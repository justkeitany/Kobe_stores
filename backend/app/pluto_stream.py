"""
Pluto TV stitch-URL resolver.

Channels imported from Pluto store the bare stitch base URL (query string
stripped), e.g.

    http://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/stitch/hls/channel/<id>/master.m3u8

Pluto's stitcher answers that URL with 400 Bad Request unless the standard
session query parameters are present. ``resolve`` appends them, minting fresh
deviceId/sid UUIDs per call so each playback session is distinct.
"""
import uuid
from urllib.parse import urlencode

# Marker that identifies a Pluto stitch URL regardless of the regional stitcher
# host (use1, use2, …) it was issued from.
_PLUTO_MARKER = "pluto.tv/stitch/hls/channel/"


def is_pluto_url(url: str | None) -> bool:
    return bool(url) and _PLUTO_MARKER in url


def resolve(url: str) -> str:
    """Append Pluto's required session params to a base stitch URL.

    The base URL is taken as-is; only query parameters are appended (any existing
    query string is replaced). Non-Pluto URLs are returned unchanged.
    """
    if not is_pluto_url(url):
        return url
    base = url.split("?", 1)[0]
    params = {
        "appName": "web",
        "appVersion": "unknown",
        "clientTime": "0",
        "deviceDNT": "0",
        "deviceId": str(uuid.uuid4()),
        "deviceMake": "Chrome",
        "deviceModel": "web",
        "deviceType": "web",
        "deviceVersion": "unknown",
        "includeExtendedEvents": "false",
        "serverSideAds": "false",
        "sid": str(uuid.uuid4()),
    }
    return f"{base}?{urlencode(params)}"
