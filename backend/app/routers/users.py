from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_admin
from app.database import get_db, Base
import secrets, string

router = APIRouter(prefix="/api/users", tags=["users"])


# ── Inline model (extends Base) ─────────────────────────────────
class IPTVUser(Base):
    __tablename__ = "iptv_users"

    id             = Column(Integer, primary_key=True, index=True)
    username       = Column(String(100), unique=True, nullable=False, index=True)
    password       = Column(String(255), nullable=False)
    max_connections = Column(Integer, default=1)
    expires_at     = Column(DateTime(timezone=True), nullable=True)
    is_active      = Column(Boolean, default=True)
    bouquet_id     = Column(Integer, ForeignKey("bouquets.id", ondelete="SET NULL"), nullable=True)
    notes          = Column(Text, nullable=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    updated_at     = Column(DateTime(timezone=True), onupdate=func.now())


# ── Schemas ─────────────────────────────────────────────────────
class UserCreate(BaseModel):
    username: str
    password: str
    max_connections: int = 1
    expires_at: Optional[str] = None   # ISO date string YYYY-MM-DD
    bouquet_id: Optional[int] = None
    notes: Optional[str] = None


class UserUpdate(BaseModel):
    password: Optional[str] = None
    max_connections: Optional[int] = None
    expires_at: Optional[str] = None
    bouquet_id: Optional[int] = None
    notes: Optional[str] = None
    is_active: Optional[bool] = None


def _parse_expires(val: Optional[str]) -> Optional[datetime]:
    if not val:
        return None
    try:
        return datetime.fromisoformat(val)
    except ValueError:
        return None


# ── Routes ──────────────────────────────────────────────────────
@router.get("")
async def list_users(
    search: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    q = select(IPTVUser).order_by(IPTVUser.created_at.desc())
    if search:
        q = q.where(IPTVUser.username.ilike(f"%{search}%"))
    result = await db.execute(q)
    users = result.scalars().all()
    return [
        {
            "id": u.id,
            "username": u.username,
            "password": u.password,
            "max_connections": u.max_connections,
            "expires_at": u.expires_at.isoformat() if u.expires_at else None,
            "is_active": u.is_active,
            "bouquet_id": u.bouquet_id,
            "notes": u.notes,
            "created_at": u.created_at,
        }
        for u in users
    ]


@router.post("", status_code=201)
async def create_user(
    data: UserCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    # Check username unique
    existing = await db.execute(select(IPTVUser).where(IPTVUser.username == data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(400, f"Username '{data.username}' already exists")

    user = IPTVUser(
        username=data.username,
        password=data.password,
        max_connections=data.max_connections,
        expires_at=_parse_expires(data.expires_at),
        bouquet_id=data.bouquet_id,
        notes=data.notes,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@router.put("/{user_id}")
async def update_user(
    user_id: int,
    data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(IPTVUser).where(IPTVUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")

    updates = data.model_dump(exclude_none=True)
    if "expires_at" in updates:
        updates["expires_at"] = _parse_expires(updates["expires_at"])
    for k, v in updates.items():
        setattr(user, k, v)

    await db.commit()
    await db.refresh(user)
    return user


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(IPTVUser).where(IPTVUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    await db.delete(user)
    await db.commit()


@router.post("/{user_id}/toggle")
async def toggle_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(IPTVUser).where(IPTVUser.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(404, "User not found")
    user.is_active = not user.is_active
    await db.commit()
    return {"is_active": user.is_active}
