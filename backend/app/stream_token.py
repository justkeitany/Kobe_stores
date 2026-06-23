"""Short, opaque, expiring tokens for the in-panel web player.

A watch link used to carry the upstream source URL and Xtream credentials in
plaintext (e.g. /live/pl/user/pass?url=https://upstream.m3u8), which let anyone
copy the real feed or reuse the link forever. Instead we hand the player an
encrypted Fernet token that resolves — server-side only — to either an imported
stream id or an upstream URL. Fernet encrypts the payload (so the URL/creds are
hidden, not just signed) and stamps a timestamp we verify against a TTL.

The key is derived from the existing JWT_SECRET so there is nothing extra to
configure per install (this product ships to many self-hosted domains).
"""

import base64
import hashlib
import json
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

DEFAULT_TTL = 6 * 3600  # 6 hours


def _fernet() -> Fernet:
    key = base64.urlsafe_b64encode(
        hashlib.sha256((settings.JWT_SECRET + ":stream-token").encode()).digest()
    )
    return Fernet(key)


def sign_stream_token(payload: dict) -> str:
    """Encrypt a payload (e.g. {"sid": 123} or {"u": "https://..."}) into a token."""
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return _fernet().encrypt(raw).decode()


def verify_stream_token(token: str, ttl: int = DEFAULT_TTL) -> Optional[dict]:
    """Return the payload if the token is valid and unexpired, else None."""
    try:
        raw = _fernet().decrypt(token.encode(), ttl=ttl)
        return json.loads(raw)
    except (InvalidToken, ValueError):
        return None
