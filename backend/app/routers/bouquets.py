from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.auth import get_current_admin
from app.database import get_db
from app.models import Bouquet, BouquetCategory, StreamCategory

router = APIRouter(prefix="/api/bouquets", tags=["bouquets"])


class BouquetCreate(BaseModel):
    name: str
    description: Optional[str] = None


class BouquetUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class AssignCategories(BaseModel):
    category_ids: list[int]


@router.get("")
async def list_bouquets(
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Bouquet).order_by(Bouquet.id))
    bouquets = result.scalars().all()

    out = []
    for b in bouquets:
        cats_res = await db.execute(
            select(BouquetCategory, StreamCategory)
            .join(StreamCategory, BouquetCategory.category_id == StreamCategory.id)
            .where(BouquetCategory.bouquet_id == b.id)
            .order_by(BouquetCategory.sort_order)
        )
        cats = [
            {"id": cat.id, "name": cat.name, "sort_order": bc.sort_order}
            for bc, cat in cats_res.all()
        ]
        out.append({
            "id": b.id,
            "name": b.name,
            "description": b.description,
            "categories": cats,
            "created_at": b.created_at,
        })
    return out


@router.post("", status_code=201)
async def create_bouquet(
    data: BouquetCreate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    b = Bouquet(**data.model_dump())
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b


@router.put("/{bouquet_id}")
async def update_bouquet(
    bouquet_id: int,
    data: BouquetUpdate,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Bouquet).where(Bouquet.id == bouquet_id))
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Bouquet not found")
    for k, v in data.model_dump(exclude_none=True).items():
        setattr(b, k, v)
    await db.commit()
    await db.refresh(b)
    return b


@router.post("/{bouquet_id}/categories")
async def assign_categories(
    bouquet_id: int,
    data: AssignCategories,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    # Verify bouquet exists
    result = await db.execute(select(Bouquet).where(Bouquet.id == bouquet_id))
    if not result.scalar_one_or_none():
        raise HTTPException(404, "Bouquet not found")

    # Replace all category assignments
    await db.execute(
        delete(BouquetCategory).where(BouquetCategory.bouquet_id == bouquet_id)
    )
    for i, cat_id in enumerate(data.category_ids):
        bc = BouquetCategory(bouquet_id=bouquet_id, category_id=cat_id, sort_order=i)
        db.add(bc)
    await db.commit()
    return {"ok": True, "assigned": len(data.category_ids)}


@router.delete("/{bouquet_id}", status_code=204)
async def delete_bouquet(
    bouquet_id: int,
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_admin),
):
    result = await db.execute(select(Bouquet).where(Bouquet.id == bouquet_id))
    b = result.scalar_one_or_none()
    if not b:
        raise HTTPException(404, "Bouquet not found")
    await db.delete(b)
    await db.commit()
