"""
cdnlivetv.tv stream resolver (ntv.cx backend).

Channels imported from ntv.cx store the player page URL, e.g.

    https://cdnlivetv.tv/api/v1/channels/player/?name=ABC&code=us&user=ntvstream&plan=free

The player page returns HTML containing a JS function that decodes and concatenates
base64 fragments into the playable stream URL. The function identifier and var names
are randomized on every fetch, but the structure is stable. This resolver fetches
the page, parses generically (identifier-agnostic), and returns the minted playlist
URL: ``https://cdnlivetv.tv/secure/api/v1/<streamid>/playlist.m3u8?token=<base64>``.

Tokens are host-bound (`cdnlivetv.tv`) and expire in 3 hours. Cached for 2h per
player URL (safely under token life), so FFmpeg restarts within the cache window
get the same URL instantly; past 2h a fresh fetch re-mints.
"""
import hashlib
import logging
import re
from base64 import b64decode

import httpx

logger = logging.getLogger(__name__)

_PLAYER_MARKER = "cdnlivetv.tv/api/v1/channels/player/"
_REFERER = "https://ntv.cx/"
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_CACHE_TTL = 7200  # 2h in seconds


def is_cdnlive_url(url: str | None) -> bool:
    return bool(url) and _PLAYER_MARKER in url


def _cache_key(url: str) -> str:
    return f"cdnlive:{hashlib.sha1(url.encode()).hexdigest()}"


def _urlsafe_b64_decode(s: str) -> str:
    """Decode a urlsafe base64 string (- → +, _ → /, auto-pad)."""
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (-len(s) % 4)
    try:
        return b64decode(s).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _parse_player_html(html: str) -> str | None:
    """Extract the concatenated stream URL from the obfuscated player page.

    Returns the decoded playlist URL or None if parsing fails. The page structure:
        function XXXX(s){ s=s.replace(/-/g,'+')... return atob(s) }
        var v1='aHR0cHM'; var v2='Og'; ...
        RESULT = XXXX(v1)+XXXX(v2)+...

    Identifiers (XXXX, v1, v2) are randomized per fetch, so we match generically:
      1. Find the concat expression: `(\w+)=((?:\w+\(\w+\)\+?){3,})`
      2. Extract the decode function name + ordered var list
      3. Pull each `var NAME='VALUE'` from the page
      4. Decode + concat
    """
    # Step 1: find the concat assignment (9+ decode calls joined by +)
    concat_match = re.search(r"(\w+)=((?:\w+\(\w+\)\+?){9,})", html)
    if not concat_match:
        logger.warning("cdnlive parse: no concat expression found")
        return None

    concat_expr = concat_match.group(2)
    # Extract function calls: FUNC(VAR) → capture VAR names in order
    var_names = re.findall(r"\w+\((\w+)\)", concat_expr)
    if len(var_names) < 9:
        logger.warning("cdnlive parse: concat too short (%d fragments)", len(var_names))
        return None

    # Step 2: pull all `var NAME='VALUE';` assignments into a dict
    var_dict = {
        name: val
        for name, val in re.findall(r"var (\w+)='([A-Za-z0-9_=-]+)';", html)
    }

    # Step 3: decode + concat in the order they appear in the concat expression
    fragments = []
    for vname in var_names:
        val = var_dict.get(vname)
        if not val:
            logger.warning("cdnlive parse: var %s not found in page", vname)
            return None
        decoded = _urlsafe_b64_decode(val)
        if not decoded:
            logger.warning("cdnlive parse: b64 decode failed for var %s", vname)
            return None
        fragments.append(decoded)

    url = "".join(fragments)
    # Sanity check: must be a cdnlivetv playlist URL
    if not url.startswith("https://cdnlivetv.tv/secure/") or "playlist.m3u8" not in url:
        logger.warning("cdnlive parse: result doesn't look like a playlist URL: %s", url[:80])
        return None

    return url


async def resolve(player_url: str) -> str | None:
    """Resolve a cdnlivetv player page URL to the playable .m3u8 stream URL.

    Returns the minted playlist URL (cached 2h) or None on any fetch/parse failure.
    Non-cdnlive URLs return None immediately.
    """
    if not is_cdnlive_url(player_url):
        return None

    from app.redis_client import get_redis

    try:
        r = await get_redis()
        cached = await r.get(_cache_key(player_url))
        if cached:
            return cached
    except Exception as e:
        logger.warning("cdnlive resolve: redis check failed: %s", e)

    # Fetch the player page
    try:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(
                player_url,
                headers={"Referer": _REFERER, "User-Agent": _BROWSER_UA},
            )
        if resp.status_code != 200:
            logger.warning(
                "cdnlive resolve: player page HTTP %s for %s",
                resp.status_code,
                player_url[:80],
            )
            return None
        html = resp.text
    except Exception as e:
        logger.warning("cdnlive resolve: fetch failed for %s: %s", player_url[:80], e)
        return None

    # Parse
    stream_url = _parse_player_html(html)
    if not stream_url:
        return None

    # Cache + return
    try:
        r = await get_redis()
        await r.setex(_cache_key(player_url), _CACHE_TTL, stream_url)
    except Exception as e:
        logger.warning("cdnlive resolve: redis cache failed: %s", e)

    logger.info("cdnlive resolve: minted %s", stream_url[:80])
    return stream_url
