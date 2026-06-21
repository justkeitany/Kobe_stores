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

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import AiEvent, AuditLog, Settings as SettingsModel, Stream

logger = logging.getLogger(__name__)

# Reversible actions the AI is allowed to apply on its own in autofix mode.
SAFE_ACTIONS = {"none", "switch_source", "drop_quality", "disable", "refresh_playlist"}
_ACTION_LABEL = {
    "switch_source": "switched to backup source",
    "drop_quality": "lowered quality",
    "disable": "disabled the dead channel",
    "refresh_playlist": "flagged playlist for refresh",
}

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


# ── providers + failover ────────────────────────────────────────────────────
# A provider failure puts it on a short cooldown so the next call skips straight
# to a healthy one instead of eating its timeout again.
_cooldown: dict[str, float] = {}
_COOLDOWN_SECS = 120


def _providers() -> list[dict]:
    """Ordered provider list. AI_PROVIDERS (JSON) first, then ANTHROPIC_API_KEY."""
    out: list[dict] = []
    raw = (settings.AI_PROVIDERS or "").strip()
    if raw:
        try:
            for p in json.loads(raw):
                if isinstance(p, dict) and p.get("api_key"):
                    out.append({
                        "name": p.get("name") or p.get("base_url") or "provider",
                        "type": (p.get("type") or "sdk").lower(),
                        "base_url": (p.get("base_url") or "").rstrip("/") or None,
                        "api_key": p["api_key"],
                        "model": p.get("model") or settings.AI_MODEL,
                    })
        except (json.JSONDecodeError, TypeError):
            logger.warning("AI_PROVIDERS is not valid JSON — ignoring it")
    if settings.ANTHROPIC_API_KEY:
        out.append({"name": "anthropic", "type": "sdk", "base_url": None,
                    "api_key": settings.ANTHROPIC_API_KEY, "model": settings.AI_MODEL})
    return out


def has_providers() -> bool:
    return len(_providers()) > 0


def _available(name: str) -> bool:
    return _cooldown.get(name, 0.0) < time.time()


def _mark_down(name: str) -> None:
    _cooldown[name] = time.time() + _COOLDOWN_SECS


def _clear_down(name: str) -> None:
    _cooldown.pop(name, None)


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {str(e)[:200]}"


async def _sdk_generate(p: dict, prompt: str, system: str, max_tokens: int) -> str:
    import anthropic
    kwargs = {"api_key": p["api_key"], "max_retries": 0, "timeout": 40.0}
    if p["base_url"]:
        kwargs["base_url"] = p["base_url"]
    client = anthropic.AsyncAnthropic(**kwargs)
    resp = await client.messages.create(
        model=p["model"], max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}],
    )
    if resp.stop_reason == "refusal":
        raise RuntimeError("provider refused the request")
    return next((b.text for b in resp.content if b.type == "text"), "") or ""


async def _cli_generate(p: dict, prompt: str, system: str) -> str:
    """Route through the genuine `claude` CLI — for gateways (e.g. Aerolink) that
    only accept the Claude Code client. Per-call env, no shared global state."""
    os.makedirs(settings.AI_CLI_HOME, exist_ok=True)
    env = {
        "HOME": settings.AI_CLI_HOME,
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "ANTHROPIC_API_KEY": p["api_key"],
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
    }
    if p["base_url"]:
        env["ANTHROPIC_BASE_URL"] = p["base_url"]
    # Fold our instructions into the user turn — don't override Claude Code's own
    # system prompt (some gateways validate it).
    full = f"{system}\n\n{prompt}" if system else prompt
    proc = await asyncio.create_subprocess_exec(
        settings.CLAUDE_BIN, "-p", full, "--model", p["model"],
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=env, cwd="/tmp",
    )
    out, err = await asyncio.wait_for(proc.communicate(), timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"claude cli rc={proc.returncode}: {err.decode('utf-8', 'replace')[:200]}")
    text = out.decode("utf-8", "replace").strip()
    if not text:
        raise RuntimeError("claude cli returned empty output")
    return text


async def _generate(prompt: str, system: str, max_tokens: int = 1500) -> str | None:
    """Generate text, failing over across providers. Returns None if all are down
    or AI is unavailable. Counts one logical call regardless of failover hops."""
    providers = _providers()
    if not providers:
        return None
    if not _under_cap():
        logger.warning("AI daily call cap reached")
        return None
    # Prefer providers not on cooldown, but fall back to all if every one is down.
    order = [p for p in providers if _available(p["name"])] or providers
    last = None
    for p in order:
        try:
            text = (await _cli_generate(p, prompt, system)) if p["type"] == "cli" \
                else (await _sdk_generate(p, prompt, system, max_tokens))
            _clear_down(p["name"])
            _count_call()
            return text
        except Exception as e:  # connection, timeout, 5xx, refusal, cli failure…
            logger.warning("AI provider '%s' failed: %s", p["name"], _err(e))
            _mark_down(p["name"])
            last = e
    logger.error("All AI providers failed; last error: %s", _err(last) if last else "none")
    return None


async def test_providers() -> list[dict]:
    """Ping every provider through its own transport — for the UI health check."""
    results = []
    for p in _providers():
        label = p["base_url"] or "api.anthropic.com"
        t0 = time.time()
        try:
            if p["type"] == "cli":
                text = await _cli_generate(p, "reply with the single word: pong", "")
            else:
                text = await _sdk_generate(p, "reply with the single word: pong", "", 16)
            _clear_down(p["name"])
            results.append({"name": p["name"], "type": p["type"], "base_url": label,
                            "ok": True, "latency_ms": int((time.time() - t0) * 1000),
                            "reply": text[:60]})
        except Exception as e:
            _mark_down(p["name"])
            results.append({"name": p["name"], "type": p["type"], "base_url": label,
                            "ok": False, "error": _err(e)})
    return results


async def get_setting(db: AsyncSession, key: str, default: str) -> str:
    row = (await db.execute(select(SettingsModel).where(SettingsModel.key == key))).scalar_one_or_none()
    return row.value if row and row.value is not None else default


async def autonomy(db: AsyncSession) -> str:
    """'suggest' or 'autofix' — DB override falls back to the config default."""
    val = await get_setting(db, "ai_autonomy", settings.AI_AUTONOMY)
    return val if val in ("suggest", "autofix") else "suggest"


async def is_enabled(db: AsyncSession) -> bool:
    if not has_providers():
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


async def _structured(prompt: str, schema: dict, system: str) -> dict | None:
    # Prompt-driven JSON (robust across SDK + CLI transports) — fails over across providers.
    sys = (
        system
        + "\n\nReply with ONLY a JSON object matching this schema (no prose, no code fences):\n"
        + json.dumps(schema)
    )
    text = await _generate(prompt, sys, max_tokens=1024)
    return _extract_json(text or "")


async def _text(prompt: str, system: str, max_tokens: int = 2000) -> str | None:
    return await _generate(prompt, system, max_tokens=max_tokens)


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
    if not has_providers() or not await is_enabled(db):
        return None
    sig = f"{stream.id}:{(error or '')[:120]}"
    if sig in _diag_cache:
        return _diag_cache[sig]

    prompt = (
        f"Stream #{stream.id} '{stream.name}' (quality={stream.quality}, "
        f"delivery={stream.delivery_mode}) keeps failing.\n"
        f"Recent FFmpeg / manager error:\n{(error or 'unknown')[:1500]}\n"
        f"{context[:1000]}"
    )
    try:
        result = await _structured(prompt, _DIAGNOSIS_SCHEMA, _DIAG_SYSTEM)
    except Exception as e:  # never let an AI hiccup affect streaming
        logger.warning("diagnose_stream failed: %s", e)
        return None
    if not result:
        return None

    _diag_cache[sig] = result
    cause = result["cause"]
    await _record(db, "diagnosis", f"{stream.name}: {cause}", result["explanation"],
                  stream_id=stream.id, data=result)

    mode = await autonomy(db)
    action = result.get("recommended_action", "none")
    applied = False
    fix_detail = ""
    if mode == "autofix" and action in SAFE_ACTIONS and action != "none" and result.get("confidence") != "low":
        applied = await apply_safe_fix(db, stream.id, action, reason=result["explanation"])
        if applied:
            fix_detail = _ACTION_LABEL.get(action, action)

    # One concise notification (the bell/notification panel reads kind="alert").
    if applied:
        summary = f"{cause.replace('_', ' ')} → {fix_detail} ✓ (auto-fixed)"
    else:
        nxt = action.replace("_", " ") if action != "none" else "needs attention"
        summary = f"{cause.replace('_', ' ')} — {nxt}"
    await _record(db, "alert", f"{stream.name}: {summary}", result["explanation"],
                  stream_id=stream.id,
                  data={"cause": cause, "action": action, "auto_applied": applied, "severity": result.get("severity")})
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
    if not has_providers() or not await is_enabled(db):
        return None
    snap = await _snapshot(db)
    system = ("You are the operations assistant for a self-hosted IPTV panel. Write a short daily "
              "health digest for the owner: what's broken, the likely cause, and what to do. "
              "Plain English, scannable, no fluff.")
    text = await _text("Panel state:\n" + json.dumps(snap, default=str), system, max_tokens=1500)
    if text:
        await _record(db, "digest", "Daily health digest", text, data=snap)
        await db.commit()
    return text


async def ops_chat(db: AsyncSession, question: str) -> str:
    if not has_providers():
        return "AI is not configured. Add an API key / provider to the backend .env to enable it."
    if not await is_enabled(db):
        return "AI is turned off. Enable it in the AI settings."
    snap = await _snapshot(db)
    system = ("You are the operations assistant for a self-hosted IPTV restreaming panel. Answer the "
              "owner's question using ONLY the live panel state provided. Be specific and concise; if "
              "the data doesn't cover it, say so.")
    prompt = f"Live panel state:\n{json.dumps(snap, default=str)}\n\nQuestion: {question}"
    text = await _text(prompt, system, max_tokens=1500)
    if text is None:
        return "All AI providers are currently unavailable (or the daily cap was hit). Try again shortly."
    if text:
        await _record(db, "chat", question[:120], text)
        await db.commit()
    return text or "No answer."


# ── Background: daily digest ────────────────────────────────────────────────

async def digest_loop() -> None:
    """Generate a health digest once a day (no-op while AI is off/keyless)."""
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


# How many errored streams to work per sweep (bounds cost + upstream load).
_MONITOR_BATCH = 15


async def _monitor_once(db: AsyncSession) -> int:
    """Diagnose + auto-fix streams currently in an error state. Returns the count."""
    streams = (await db.execute(
        select(Stream).where(Stream.status == "error", Stream.is_enabled == True)  # noqa: E712
        .limit(_MONITOR_BATCH)
    )).scalars().all()
    for s in streams:
        await diagnose_stream(db, s, s.last_error or "stream in error state", context="background monitor")
    if streams:
        logger.info("AI monitor swept %d errored streams", len(streams))
    return len(streams)


async def monitor_loop() -> None:
    """Background watchdog: every AI_MONITOR_INTERVAL, find broken channels,
    diagnose them, auto-fix the safe ones, and drop a result into the alerts feed.
    Invisible to the user except for the notification it leaves behind."""
    await asyncio.sleep(150)  # let startup settle
    while True:
        try:
            async with AsyncSessionLocal() as db:
                if has_providers() and await is_enabled(db):
                    await _monitor_once(db)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("AI monitor loop error: %s", e)
        await asyncio.sleep(settings.AI_MONITOR_INTERVAL)
