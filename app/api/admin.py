"""Admin endpoints for depreciation config management.

All endpoints require the X-Admin-Key header matching ADMIN_API_KEY in settings.
These are the only way to update depreciation data without touching the database directly.

Routes:
  GET    /admin/depreciation/categories              list all categories
  PUT    /admin/depreciation/categories/{name}       update curve or range band
  GET    /admin/depreciation/brands                  list all brand modifiers
  POST   /admin/depreciation/brands                  add a new brand
  PUT    /admin/depreciation/brands/{brand_name}     update modifier or source note
  DELETE /admin/depreciation/brands/{brand_name}     remove a brand
  GET    /admin/depreciation/variants                list all variant overrides
  POST   /admin/depreciation/variants                add a new variant override
  PUT    /admin/depreciation/variants/{id}           update a variant override
  DELETE /admin/depreciation/variants/{id}           remove a variant override
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Security
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.models.db_models import BrandModifier, DepreciationCategory, VariantOverride

router = APIRouter(prefix="/admin/depreciation", tags=["admin"])

_api_key_header = APIKeyHeader(name="X-Admin-Key", auto_error=True)

# Modifier bounds — prevents a single bad update from producing nonsensical estimates.
# ±0.50 is intentionally wide (Land Rover is -0.10, Porsche is +0.08 in seed data)
# so there is room for future outliers, but ±0.50 would already be economically implausible.
_MODIFIER_MIN = -0.50
_MODIFIER_MAX = 0.50


async def require_admin(key: str = Security(_api_key_header)) -> None:
    if not settings.admin_api_key:
        raise HTTPException(status_code=503, detail="Admin API not configured")
    if key != settings.admin_api_key:
        raise HTTPException(status_code=403, detail="Invalid admin key")


# ---------------------------------------------------------------------------
# Pydantic request/response models (admin-only, separate from agent schemas)
# ---------------------------------------------------------------------------


class CategoryUpdate(BaseModel):
    curve_values: list[float] | None = None
    range_band: float | None = Field(None, ge=0.0, le=0.50)
    source_note: str | None = None

    @field_validator("curve_values")
    @classmethod
    def validate_curve(cls, v: list[float] | None) -> list[float] | None:
        if v is None:
            return v
        if len(v) != 11:
            raise ValueError("curve_values must have exactly 11 elements (years 0–10)")
        if v[0] != 1.0:
            raise ValueError("curve_values[0] must be 1.0 — year-0 is always full MSRP")
        if any(val < 0.0 or val > 1.0 for val in v):
            raise ValueError("All curve values must be between 0.0 and 1.0")
        if any(v[i] < v[i + 1] for i in range(len(v) - 1)):
            raise ValueError("curve_values must be non-increasing — vehicles do not appreciate in this model")
        return v


class BrandCreate(BaseModel):
    brand_name: str = Field(..., min_length=1, max_length=64)
    modifier: float = Field(..., ge=_MODIFIER_MIN, le=_MODIFIER_MAX)
    segment: str = Field("all", pattern="^(everyday|luxury|enthusiast|all)$")
    source_note: str = Field(..., min_length=10)


class BrandUpdate(BaseModel):
    modifier: float | None = Field(None, ge=_MODIFIER_MIN, le=_MODIFIER_MAX)
    segment: str | None = Field(None, pattern="^(everyday|luxury|enthusiast|all)$")
    source_note: str | None = Field(None, min_length=10)


class VariantCreate(BaseModel):
    make: str = Field(..., min_length=1, max_length=64)
    model_keywords: list[str] = Field(..., min_length=1)
    engine_keywords: list[str] = []
    year_from: int | None = Field(None, ge=1900, le=2100)
    year_to: int | None = Field(None, ge=1900, le=2100)
    modifier: float = Field(..., ge=_MODIFIER_MIN, le=_MODIFIER_MAX)
    notes: str = Field(..., min_length=10)
    source_note: str = Field(..., min_length=10)

    @model_validator(mode="after")
    def year_range_valid(self) -> VariantCreate:
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must be less than or equal to year_to")
        return self


class VariantUpdate(BaseModel):
    model_keywords: list[str] | None = Field(None, min_length=1)
    engine_keywords: list[str] | None = None
    year_from: int | None = Field(None, ge=1900, le=2100)
    year_to: int | None = Field(None, ge=1900, le=2100)
    modifier: float | None = Field(None, ge=_MODIFIER_MIN, le=_MODIFIER_MAX)
    notes: str | None = Field(None, min_length=10)
    source_note: str | None = Field(None, min_length=10)

    @model_validator(mode="after")
    def year_range_valid(self) -> VariantUpdate:
        if self.year_from and self.year_to and self.year_from > self.year_to:
            raise ValueError("year_from must be less than or equal to year_to")
        return self


# ---------------------------------------------------------------------------
# Categories — read + update only (names are stable identifiers, no add/delete)
# ---------------------------------------------------------------------------


@router.get("/categories", dependencies=[Depends(require_admin)])
async def list_categories(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(DepreciationCategory))).scalars().all()
    return [
        {
            "name": r.name,
            "curve_values": r.curve_values,
            "range_band": r.range_band,
            "source_note": r.source_note,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.put("/categories/{name}", dependencies=[Depends(require_admin)])
async def update_category(
    name: str, body: CategoryUpdate, db: AsyncSession = Depends(get_db)
) -> dict[str, Any]:
    row = await db.scalar(
        select(DepreciationCategory).where(DepreciationCategory.name == name)
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Category '{name}' not found")

    if body.curve_values is not None:
        row.curve_values = body.curve_values
    if body.range_band is not None:
        row.range_band = body.range_band
    if body.source_note is not None:
        row.source_note = body.source_note

    await db.commit()
    return {"updated": name}


# ---------------------------------------------------------------------------
# Brand modifiers — full CRUD
# ---------------------------------------------------------------------------


@router.get("/brands", dependencies=[Depends(require_admin)])
async def list_brands(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(BrandModifier).order_by(BrandModifier.brand_name))).scalars().all()
    return [
        {
            "id": str(r.id),
            "brand_name": r.brand_name,
            "modifier": r.modifier,
            "segment": r.segment,
            "source_note": r.source_note,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.post("/brands", dependencies=[Depends(require_admin)], status_code=201)
async def create_brand(body: BrandCreate, db: AsyncSession = Depends(get_db)) -> dict:
    existing = await db.scalar(
        select(BrandModifier).where(BrandModifier.brand_name == body.brand_name.lower())
    )
    if existing:
        raise HTTPException(status_code=409, detail=f"Brand '{body.brand_name}' already exists")

    row = BrandModifier(
        brand_name=body.brand_name.lower(),
        modifier=body.modifier,
        segment=body.segment,
        source_note=body.source_note,
    )
    db.add(row)
    await db.commit()
    return {"created": body.brand_name.lower(), "id": str(row.id)}


@router.put("/brands/{brand_name}", dependencies=[Depends(require_admin)])
async def update_brand(
    brand_name: str, body: BrandUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    row = await db.scalar(
        select(BrandModifier).where(BrandModifier.brand_name == brand_name.lower())
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Brand '{brand_name}' not found")

    if body.modifier is not None:
        row.modifier = body.modifier
    if body.segment is not None:
        row.segment = body.segment
    if body.source_note is not None:
        row.source_note = body.source_note

    await db.commit()
    return {"updated": brand_name.lower()}


@router.delete("/brands/{brand_name}", dependencies=[Depends(require_admin)])
async def delete_brand(brand_name: str, db: AsyncSession = Depends(get_db)) -> dict:
    row = await db.scalar(
        select(BrandModifier).where(BrandModifier.brand_name == brand_name.lower())
    )
    if not row:
        raise HTTPException(status_code=404, detail=f"Brand '{brand_name}' not found")

    await db.delete(row)
    await db.commit()
    return {"deleted": brand_name.lower()}


# ---------------------------------------------------------------------------
# Variant overrides — full CRUD
# ---------------------------------------------------------------------------


@router.get("/variants", dependencies=[Depends(require_admin)])
async def list_variants(db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(VariantOverride).order_by(VariantOverride.make))).scalars().all()
    return [
        {
            "id": str(r.id),
            "make": r.make,
            "model_keywords": r.model_keywords,
            "engine_keywords": r.engine_keywords,
            "year_from": r.year_from,
            "year_to": r.year_to,
            "modifier": r.modifier,
            "notes": r.notes,
            "source_note": r.source_note,
            "updated_at": r.updated_at,
        }
        for r in rows
    ]


@router.post("/variants", dependencies=[Depends(require_admin)], status_code=201)
async def create_variant(body: VariantCreate, db: AsyncSession = Depends(get_db)) -> dict:
    row = VariantOverride(
        make=body.make.lower(),
        model_keywords=[k.lower() for k in body.model_keywords],
        engine_keywords=[k.lower() for k in body.engine_keywords],
        year_from=body.year_from,
        year_to=body.year_to,
        modifier=body.modifier,
        notes=body.notes,
        source_note=body.source_note,
    )
    db.add(row)
    await db.commit()
    return {"created": str(row.id)}


@router.put("/variants/{variant_id}", dependencies=[Depends(require_admin)])
async def update_variant(
    variant_id: uuid.UUID, body: VariantUpdate, db: AsyncSession = Depends(get_db)
) -> dict:
    row = await db.get(VariantOverride, variant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Variant override not found")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(row, field, value)

    await db.commit()
    return {"updated": str(variant_id)}


@router.delete("/variants/{variant_id}", dependencies=[Depends(require_admin)])
async def delete_variant(
    variant_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> dict:
    row = await db.get(VariantOverride, variant_id)
    if not row:
        raise HTTPException(status_code=404, detail="Variant override not found")

    await db.delete(row)
    await db.commit()
    return {"deleted": str(variant_id)}
