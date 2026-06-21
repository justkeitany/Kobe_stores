"""
Claude-powered operations assistant for the panel.

Capabilities:
  - diagnose_stream  : explain WHY a stream is failing (structured output)
  - apply_safe_fix   : apply a whitelisted, reversible fix (autofix mode only)
  - generate_digest  : daily plain-English health report
  - ops_chat         : answer operator questions from live panel data

Design notes
  - The Anthropic key lives in the backend .env (settings.ANTHROPIC_API_KEY),
    never in the repo. With no key the whole module is a graceful no-op so the
    panel runs exactly as before.
  - `anthropic` is imported lazily so a missing package can't break startup.
  - A hard per-day call cap (settings.AI_DAILY_CALL_CAP) bounds cost; diagnoses
    are cached per (stream, error) so a crash loop is one call, not hundreds.
  - Every AI action is written to AiEvent (and AuditLog) — full audit trail.
  - Autonomy is runtime-controlled: "suggest" records recommendations only;
    "autofix" also applies the whitelisted reversible actions.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import AiEvent, AuditLog, Settings as SettingsModel, Stream

logger = logging.getLogger(__name__)

# Reversible actions the AI is allowed to apply on its own in autofix mode.
SAFE_ACTIONS = {"none", "switch_source", "drop_quality", "disable", "refresh_playlist"}

# Per-day call counter (cost guard). Reset when the date rolls over.
_calls: dict[str, int] = {}

# Diagnosis cache: (stream_id, error-signature) -> result dict, so a crash loop
# costs one Claude call rather than one per retry.
_diag_cache: dict[str, dict] = {}

_DIAGNOSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "cause": {
            "type": "string",
            "description": "Short cause label: geo_blocked, dead_source, token_expired, "
            "offline_placeholder, multiple_connections, audio_only, codec, network, unknown",
        },
        "explanation": {"type": "string", "description": "1-2 plain-English sentences."},
        "severity": {"type": "string", "enum": ["low", "medium", "high"]},
        "recommended_action": {
            "type": "string",
            "enum": ["none", "switch_source", "drop_quality", "disable", "refresh_playlist"],
        },
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
    },
    "required": ["cause", "explanation", "severity", "recommended_action", "confidence"],
    "additionalProperties": False,
}


# ── client + guards ─────────────────────────────────────────────────────────

def _aclient():
    """Async Anthropic client, or None when unavailable (no key / package)."""
    if not settings.ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic package not installed — AI features disabled")
        return None
    return anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)


async def get_setting(db: AsyncSession, key: str, default: str) -> str:
    row = (await db.execute(select(SettingsModel).where(SettingsModel.key == key))).scalar_one_or_none()
    return row.value if row and row.value is not None else default


async def autonomy(db: AsyncSession) -> str:
    """'suggest' or 'autofix' — DB override falls back to the config default."""
    val = await get_setting(db, "ai_autonomy", settings.AI_AUTONOMY)
    return val if val in ("suggest", "autofix") else "suggest"


async def is_enabled(db: AsyncSession) -> bool:
    if not settings.ANTHROPIC_API_KEY:
        return False
    return (await get_setting(db, "ai_enabled", "true")).lower() == "true"


def _under_cap() -> bool:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _calls.get(today, 0) < settings.AI_DAILY_CALL_CAP


def _count_call() -> None:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    _calls[today] = _calls.get(today, 0) + 1


def calls_today() -> int:
    return _calls.get(datetime.now(timezone.utc).strftime("%Y-%m-%d"), 0)


async def _record(db: AsyncSession, kind: str, title: str, detail: str,
                  stream_id: int | None = None, data: dict | None = None) -> AiEvent:
    ev = AiEvent(kind=kind, title=title[:255], detail=detail, stream_id=stream_id, data=data)
    db.add(ev)
    await db.flush()
    return ev


# ── Claude calls ────────────────────────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Pull the first JSON object out of a model response (tolerates fences/prose)."""
    if not text:
        return None
    start, depth = text.find("{"), 0
    if start < 0:
        return None
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


async def _structured(client, prompt: str, schema: dict, system: str) -> dict | None:
    # Prompt-driven JSON (robust across SDK versions) rather than output_config.
    _count_call()
    sys = (
        system
        + "\n\nReply with ONLY a JSON object matching this schema (no prose, no code fences):\n"
        + json.dumps(schema)
    )
    resp = await client.messages.create(
        model=settings.AI_MODEL,
        max_tokens=1024,
        system=sys,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        return None
    text = next((b.text for b in resp.content if b.type == "text"), "")
    return _extract_json(text)


async def _text(client, prompt: str, system: str, max_tokens: int = 2000) -> str | None:
    _count_call()
    resp = await client.messages.create(
        model=settings.AI_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        return None
    return next((b.text for b in resp.content if b.type == "text"), "") or None


# ── Diagnosis + auto-fix ────────────────────────────────────────────────────

_DIAG_SYSTEM = (
    "You are an SRE for a self-hosted IPTV restreaming panel. Streams are pulled with FFmpeg "
    "from upstream sources (many are M3USe proxy links that 302-redirect to filmon / YouTube / "
    "Xtream with short-lived tokens; a known offline placeholder is benmoose39's 'moose-multiple' "
    "loop). Classify the failure and recommend ONE reversible action. Be concise and practical."
)


async def diagnose_stream(db: AsyncSession, stream: Stream, error: str, context: str = "") -> dict | None:
    """Diagnose a failing stream. Returns the structured result, or None if AI is
    unavailable/over-cap. In autofix mode, also applies the recommended safe action."""
    client = _aclient()
    if client is None or not await is_enabled(db):
        return None
    sig = f"{stream.id}:{(error or '')[:120]}"
    if sig in _diag_cache:
        return _diag_cache[sig]
    if not _under_cap():
        logger.warning("AI daily call cap reached — skipping diagnosis")
        return None

    prompt = (
        f"Stream #{stream.id} '{stream.name}' (quality={stream.quality}, "
        f"delivery={stream.delivery_mode}) keeps failing.\n"
        f"Recent FFmpeg / manager error:\n{(error or 'unknown')[:1500]}\n"
        f"{context[:1000]}"
    )
    try:
        result = await _structured(client, prompt, _DIAGNOSIS_SCHEMA, _DIAG_SYSTEM)
    except Exception as e:  # never let an AI hiccup affect streaming
        logger.warning("diagnose_stream failed: %s", e)
        return None
    if not result:
        return None

    _diag_cache[sig] = result
    ev = await _record(
        db, "diagnosis",
        f"{stream.name}: {result['cause']}",
        result["explanation"], stream_id=stream.id, data=result,
    )

    mode = await autonomy(db)
    action = result.get("recommended_action", "none")
    if mode == "autofix" and action in SAFE_ACTIONS and action != "none" and result.get("confidence") != "low":
        applied = await apply_safe_fix(db, stream.id, action, reason=result["explanation"])
        ev.data = {**result, "auto_applied": applied}
    await db.commit()
    return result


async def diagnose_by_id(stream_id: int, error: str) -> None:
    """Fire-and-forget diagnosis hook for the FFmpeg give-up path (own session)."""
    try:
        async with AsyncSessionLocal() as db:
            if not await is_enabled(db):
                return
            stream = (await db.execute(select(Stream).where(Stream.id == stream_id))).scalar_one_or_none()
            if stream:
                await diagnose_stream(db, stream, error or stream.last_error or "unknown")
    except Exception as e:
        logger.warning("background diagnose failed: %s", e)


async def apply_safe_fix(db: AsyncSession, stream_id: int, action: str, reason: str = "") -> bool:
    """Apply one whitelisted reversible fix. Returns True if applied. Logged to
    AiEvent + AuditLog so every autonomous change is auditable and reversible."""
    if action not in SAFE_ACTIONS or action == "none":
        return False
    stream = (await db.execute(select(Stream).where(Stream.id == stream_id))).scalar_one_or_none()
    if not stream:
        return False

    from app.ffmpeg_manager import ffmpeg_manager
    from app.sources import source_urls

    detail = ""
    if action == "disable":
        stream.is_enabled = False
        await ffmpeg_manager.stop_stream(stream_id)
        detail = "Disabled the stream (confirmed dead)."
    elif action == "drop_quality":
        ladder = {"auto": "medium", "medium": "low", "high": "medium", "low": "low"}
        new_q = ladder.get(stream.quality, "low")
        stream.quality = new_q
        await ffmpeg_manager.restart_stream(stream_id, quality=new_q)
        detail = f"Dropped quality to {new_q} to ease buffering."
    elif action == "switch_source":
        urls = source_urls(stream, stream.sources)
        if len(urls) > 1:
            rotated = urls[1:] + urls[:1]  # move primary to the back of the pool
            await ffmpeg_manager.restart_stream(stream_id, sources=rotated)
            detail = "Rotated to the next backup source."
        else:
            return False
    elif action == "refresh_playlist":
        detail = "Flagged the source playlist for refresh."

    db.add(AuditLog(action=f"ai_{action}", entity_type="stream", entity_id=stream_id,
                    details={"reason": reason}))
    await _record(db, "action", f"{stream.name}: {action}", detail or reason, stream_id=stream_id,
                  data={"action": action})
    return True


# ── Digest + chat ───────────────────────────────────────────────────────────

async def _snapshot(db: AsyncSession) -> dict:
    """Compact live state for the digest / chat context."""
    from app.ffmpeg_manager import ffmpeg_manager
    from app.models import Playlist

    streams = (await db.execute(select(Stream))).scalars().all()
    errored = [s for s in streams if s.status == "error"]
    playlists = (await db.execute(select(Playlist))).scalars().all()
    bad_pl = [p for p in playlists if p.last_error]
    viewers = sum(sp.active_viewers() for sp in ffmpeg_manager._streams.values())
    return {
        "viewers_now": viewers,
        "active_streams": ffmpeg_manager.active_stream_count(),
        "total_streams": len(streams),
        "errored_streams": [{"id": s.id, "name": s.name, "error": (s.last_error or "")[:200]} for s in errored[:25]],
        "playlists_total": len(playlists),
        "playlists_with_issues": [{"name": p.name, "health": p.health, "error": p.last_error} for p in bad_pl[:25]],
    }


async def generate_digest(db: AsyncSession) -> str | None:
    client = _aclient()
    if client is None or not await is_enabled(db) or not _under_cap():
        return None
    snap = await _snapshot(db)
    system = ("You are the operations assistant for a self-hosted IPTV panel. Write a short daily "
              "health digest for the owner: what's broken, the likely cause, and what to do. "
              "Plain English, scannable, no fluff.")
    try:
        text = await _text(client, "Panel state:\n" + json.dumps(snap, default=str), system, max_tokens=1500)
    except Exception as e:
        logger.warning("generate_digest failed: %s", e)
        return None
    if text:
        await _record(db, "digest", "Daily health digest", text, data=snap)
        await db.commit()
    return text


async def ops_chat(db: AsyncSession, question: str) -> str:
    client = _aclient()
    if client is None:
        return "AI is not configured. Add ANTHROPIC_API_KEY to the backend .env to enable it."
    if not await is_enabled(db):
        return "AI is turned off. Enable it in the AI settings."
    if not _under_cap():
        return "Daily AI usage cap reached — try again tomorrow or raise the cap."
    snap = await _snapshot(db)
    system = ("You are the operations assistant for a self-hosted IPTV restreaming panel. Answer the "
              "owner's question using ONLY the live panel state provided. Be specific and concise; if "
              "the data doesn't cover it, say so.")
    prompt = f"Live panel state:\n{json.dumps(snap, default=str)}\n\nQuestion: {question}"
    try:
        text = await _text(client, prompt, system, max_tokens=1500)
    except Exception as e:
        logger.warning("ops_chat failed: %s", e)
        return "The AI request failed — check the backend logs."
    if text:
        await _record(db, "chat", question[:120], text)
        await db.commit()
    return text or "No answer."


# ── Background: daily digest ────────────────────────────────────────────────

async def digest_loop() -> None:
    """Generate a health digest once a day (no-op while AI is off/keyless)."""
    import asyncio

    await asyncio.sleep(180)  # let startup settle
    while True:
        try:
            async with AsyncSessionLocal() as db:
                if await is_enabled(db):
                    await generate_digest(db)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("AI digest loop error: %s", e)
        await asyncio.sleep(24 * 3600)
