"""Mint short-lived, encrypted play tokens for the in-panel web player.

The frontend calls POST /api/stream/token (authenticated as an admin) just
before opening the player, and navigates to /watch?t=<token>. The token hides
the upstream URL / credentials and expires — see app.stream_token.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import get_current_admin
from app.routers.xtream import is_url_allowed
from app.stream_token import sign_stream_token

router = APIRouter(prefix="/api/stream", tags=["stream"])


class TokenReq(BaseModel):
    stream_id: Optional[int] = None
    url: Optional[str] = None


@router.post("/token")
async def mint(req: TokenReq, admin=Depends(get_current_admin)):
    if req.stream_id is not None:
        payload = {"sid": req.stream_id}
    elif req.url:
        if not is_url_allowed(req.url):
            raise HTTPException(400, "URL not allowed")
        payload = {"u": req.url}
    else:
        raise HTTPException(400, "stream_id or url required")
    return {"token": sign_stream_token(payload)}
