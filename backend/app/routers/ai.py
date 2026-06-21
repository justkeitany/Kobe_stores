"""AI assistant endpoints (/api/ai) — status/settings, diagnosis, digest, chat."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import ai
from app.auth import get_current_admin
from app.config import settings
from app.database import get_db
from app.models import AiEvent, Settings as SettingsModel, Stream

router = APIRouter(prefix="/api/ai", tags=["ai"])


class AiSettings(BaseModel):
    enabled: bool | None = None
    autonomy: str | None = None  # suggest | autofix


class ChatIn(BaseModel):
    question: str


class ApplyIn(BaseModel):
    stream_id: int
    action: str


async def _set(db: AsyncSession, key: str, value: str) -> None:
    row = (await db.execute(select(SettingsModel).where(SettingsModel.key == key))).scalar_one_or_none()
    if row:
        row.value = value
    else:
        db.add(SettingsModel(key=key, value=value))


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    return {
        "key_present": bool(settings.ANTHROPIC_API_KEY),
        "enabled": await ai.is_enabled(db),
        "autonomy": await ai.autonomy(db),
        "model": settings.AI_MODEL,
        "calls_today": ai.calls_today(),
        "daily_cap": settings.AI_DAILY_CALL_CAP,
    }


@router.put("/settings")
async def update_settings(data: AiSettings, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    if data.enabled is not None:
        await _set(db, "ai_enabled", "true" if data.enabled else "false")
    if data.autonomy is not None:
        if data.autonomy not in ("suggest", "autofix"):
            raise HTTPException(400, "autonomy must be 'suggest' or 'autofix'")
        await _set(db, "ai_autonomy", data.autonomy)
    await db.commit()
    return {"enabled": await ai.is_enabled(db), "autonomy": await ai.autonomy(db)}


@router.get("/events")
async def events(kind: str | None = None, limit: int = 50,
                 db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    q = select(AiEvent).order_by(AiEvent.created_at.desc(), AiEvent.id.desc()).limit(min(limit, 200))
    if kind:
        q = q.where(AiEvent.kind == kind)
    rows = (await db.execute(q)).scalars().all()
    return [{
        "id": e.id, "kind": e.kind, "stream_id": e.stream_id, "title": e.title,
        "detail": e.detail, "data": e.data, "created_at": e.created_at,
    } for e in rows]


@router.post("/diagnose/{stream_id}")
async def diagnose(stream_id: int, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    if not await ai.is_enabled(db):
        raise HTTPException(400, "AI is not enabled (add ANTHROPIC_API_KEY and turn it on)")
    stream = (await db.execute(select(Stream).where(Stream.id == stream_id))).scalar_one_or_none()
    if not stream:
        raise HTTPException(404, "Stream not found")
    result = await ai.diagnose_stream(db, stream, stream.last_error or "No recorded error.",
                                      context=f"status={stream.status}")
    if result is None:
        raise HTTPException(502, "Diagnosis unavailable (AI off, over cap, or request failed)")
    return result


@router.get("/digest")
async def latest_digest(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    row = (await db.execute(
        select(AiEvent).where(AiEvent.kind == "digest").order_by(AiEvent.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    if not row:
        return {"detail": None}
    return {"detail": row.detail, "created_at": row.created_at, "data": row.data}


@router.post("/digest/run")
async def run_digest(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    if not await ai.is_enabled(db):
        raise HTTPException(400, "AI is not enabled")
    text = await ai.generate_digest(db)
    if text is None:
        raise HTTPException(502, "Digest unavailable (AI off, over cap, or request failed)")
    return {"detail": text}


@router.post("/chat")
async def chat(data: ChatIn, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    if not data.question.strip():
        raise HTTPException(400, "Empty question")
    return {"answer": await ai.ops_chat(db, data.question.strip())}


@router.post("/apply")
async def apply(data: ApplyIn, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    """Apply a recommended safe fix on demand (used in 'suggest' mode)."""
    ok = await ai.apply_safe_fix(db, data.stream_id, data.action, reason="manually applied from AI suggestion")
    await db.commit()
    if not ok:
        raise HTTPException(400, "Action not applicable")
    return {"ok": True}
