from typing import Optional
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_admin
from app.database import get_db
from app.models import Settings as SettingsModel

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingUpdate(BaseModel):
    key: str
    value: str


@router.get("")
async def get_settings(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(SettingsModel))
    rows = result.scalars().all()
    return {row.key: row.value for row in rows}


@router.put("")
async def update_setting(
    data: SettingUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(
        select(SettingsModel).where(SettingsModel.key == data.key)
    )
    row = result.scalar_one_or_none()
    if row:
        row.value = data.value
    else:
        row = SettingsModel(key=data.key, value=data.value)
        db.add(row)
    await db.commit()
    return {"ok": True, "key": data.key}


@router.put("/bulk")
async def bulk_update(
    data: dict,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    for key, value in data.items():
        result = await db.execute(
            select(SettingsModel).where(SettingsModel.key == key)
        )
        row = result.scalar_one_or_none()
        if row:
            row.value = str(value)
        else:
            db.add(SettingsModel(key=key, value=str(value)))
    await db.commit()
    return {"ok": True, "updated": len(data)}


# ── Proxy pool management ────────────────────────────────────────────────

from pydantic import BaseModel as PydanticBase

class ProxyPoolUpdate(PydanticBase):
    raw: str  # one "host:port:user:pass" per line (user:pass optional)

class ProxyReset(PydanticBase):
    action: str  # "reset_bandwidth"


@router.get("/proxy/pool")
async def get_proxy_pool(db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)):
    from app.proxy_resolver import parse_pool, K_MAP, K_POOL, bw_used, bw_quota
    raw = await db.execute(select(SettingsModel).where(SettingsModel.key == K_POOL))
    raw_val = raw.scalar_one_or_none()
    raw_str = raw_val.value if raw_val else ""
    proxies = parse_pool(raw_str)
    mapped = await db.execute(select(SettingsModel).where(SettingsModel.key == K_MAP))
    mapped_val = mapped.scalar_one_or_none()
    try:
        import json
        geo = json.loads(mapped_val.value) if mapped_val else []
    except Exception:
        geo = []
    return {"raw": raw_str, "count": len(proxies), "geo": geo,
            "bandwidth_used": await bw_used(), "bandwidth_quota": await bw_quota()}


@router.put("/proxy/pool")
async def update_proxy_pool(
    data: ProxyPoolUpdate, db: AsyncSession = Depends(get_db), _=Depends(get_current_admin)
):
    from app.proxy_resolver import parse_pool, K_MAP, K_POOL, detect_country
    proxies = parse_pool(data.raw)
    # Geo-locate each proxy (fire-and-forget — slow if many, cached in DB after).
    geo_tagged = []
    for p in proxies:
        cc = await detect_country(p)
        geo_tagged.append({**p, "country": cc or "??"})
    import json
    for key, val in [(K_POOL, data.raw), (K_MAP, json.dumps(geo_tagged))]:
        row = (await db.execute(select(SettingsModel).where(SettingsModel.key == key))).scalar_one_or_none()
        if row:
            row.value = val
        else:
            db.add(SettingsModel(key=key, value=val))
    await db.commit()
    return {"ok": True, "count": len(geo_tagged), "geo": geo_tagged}


@router.post("/proxy/reset")
async def reset_proxy_bw(data: ProxyReset, _=Depends(get_current_admin)):
    from app.proxy_resolver import bw_reset
    if data.action == "reset_bandwidth":
        await bw_reset()
        return {"ok": True, "bandwidth_used": 0}
    return {"ok": False, "error": "unknown action"}