"""
Pluto TV stream resolver.

Channels imported from Pluto store the bare stitch base URL, e.g.

    https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/stitch/hls/channel/<id>/master.m3u8

Pluto's own stitcher now serves a "takedown slate" instead of real video to
datacenter IPs, regardless of device/session spoofing — verified that even a
valid boot session + JWT still returns the slate from a datacenter host.

The reliable path is the public resolver ``jmp2.uk/plu-<channelID>.m3u8``
(matthuisman's service), which 302-redirects to the Samsung-TV-Plus partner
stitcher with a freshly minted partner authToken that returns real content.
``resolve`` rewrites a stored stitch URL to that resolver URL; FFmpeg follows
the redirect when it opens the stream.
"""
import re

# Matches the channel id (24 hex chars) inside any Pluto channel URL, whether
# the stored stitch form or the jmp2.uk resolver form.
_CHANNEL_ID_RE = re.compile(r"/(?:channel/|plu-)([0-9a-f]{24})", re.IGNORECASE)

# Identifies a Pluto URL regardless of the regional stitcher host it came from.
_PLUTO_MARKERS = ("pluto.tv/stitch/hls/channel/", "jmp2.uk/plu-")

_RESOLVER = "https://jmp2.uk/plu-{channel_id}.m3u8"


def is_pluto_url(url: str | None) -> bool:
    return bool(url) and any(marker in url for marker in _PLUTO_MARKERS)


def resolve(url: str) -> str:
    """Rewrite a Pluto stitch URL to the jmp2.uk resolver URL.

    Non-Pluto URLs (and Pluto URLs with no extractable channel id) are returned
    unchanged. The result is idempotent for URLs already in resolver form.
    """
    if not is_pluto_url(url):
        return url
    match = _CHANNEL_ID_RE.search(url)
    if not match:
        return url
    return _RESOLVER.format(channel_id=match.group(1).lower())
