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
