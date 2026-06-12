from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_admin
from app.database import get_db
from app.models import Stream, StreamCategory

router = APIRouter(prefix="/api/categories", tags=["categories"])


class CategoryCreate(BaseModel):
    name: str
    icon: Optional[str] = None
    sort_order: int = 0


class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    sort_order: Optional[int] = None


class ReorderItem(BaseModel):
    id: int
    sort_order: int


@router.get("")
async def list_categories(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(
        select(StreamCategory).order_by(StreamCategory.sort_order, StreamCategory.id)
    )
    cats = result.scalars().all()

    # Count streams per category
    out = []
    for cat in cats:
        count_res = await db.execute(
            select(Stream).where(Stream.category_id == cat.id)
        )
        count = len(count_res.scalars().all())
        out.append({
            "id": cat.id,
            "name": cat.name,
            "icon": cat.icon,
            "sort_order": cat.sort_order,
            "stream_count": count,
            "created_at": cat.created_at,
        })
    return out


@router.post("", status_code=201)
async def create_category(
    data: CategoryCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    cat = StreamCategory(**data.model_dump())
    db.add(cat)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.put("/reorder")
async def reorder_categories(
    items: list[ReorderItem],
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    for item in items:
        await db.execute(
            update(StreamCategory)
            .where(StreamCategory.id == item.id)
            .values(sort_order=item.sort_order)
        )
    await db.commit()
    return {"ok": True}


@router.put("/{cat_id}")
async def update_category(
    cat_id: int,
    data: CategoryUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(StreamCategory).where(StreamCategory.id == cat_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Category not found")
    updates = data.model_dump(exclude_none=True)
    for k, v in updates.items():
        setattr(cat, k, v)
    await db.commit()
    await db.refresh(cat)
    return cat


@router.delete("/{cat_id}", status_code=204)
async def delete_category(
    cat_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(StreamCategory).where(StreamCategory.id == cat_id))
    cat = result.scalar_one_or_none()
    if not cat:
        raise HTTPException(404, "Category not found")
    # Move streams to uncategorized
    await db.execute(
        update(Stream).where(Stream.category_id == cat_id).values(category_id=None)
    )
    await db.delete(cat)
    await db.commit()


@router.post("/{cat_id}/move-streams")
async def move_streams(
    cat_id: int,
    target_category_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    await db.execute(
        update(Stream)
        .where(Stream.category_id == cat_id)
        .values(category_id=target_category_id)
    )
    await db.commit()
    return {"ok": True}
