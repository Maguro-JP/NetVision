"""選んで、値を入れるだけの Config 作成。

図も台帳も資料も要らない入口。画面は献立の一覧を並べて、選ばれた献立の
入力欄を出して、入った値をそのまま送るだけでよい。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.services import configparse
from app.services import configrecipes as service
from app.services.configparse.base import Vendor

router = APIRouter(tags=["config-recipes"])


class FieldRead(BaseModel):
    name: str
    label: str
    kind: str
    required: bool
    help: str
    placeholder: str


class VendorRead(BaseModel):
    name: str
    label: str


class RecipeRead(BaseModel):
    name: str
    label: str
    why: str
    fields: list[FieldRead]
    vendors: list[VendorRead]


class RenderRequest(BaseModel):
    vendor: str
    values: dict[str, str] = Field(default_factory=dict)


class RenderRead(BaseModel):
    vendor: str
    vendor_label: str
    body: str
    notes: list[str]


def _vendor_read(vendor: Vendor) -> VendorRead:
    return VendorRead(name=str(vendor), label=configparse.VENDOR_LABELS.get(vendor, str(vendor)))


@router.get("/config-recipes", response_model=list[RecipeRead])
def list_recipes() -> list[RecipeRead]:
    """出せる献立と、その入力欄。画面はこれを並べる。"""
    return [
        RecipeRead(
            name=recipe.name,
            label=recipe.label,
            why=recipe.why,
            fields=[
                FieldRead(
                    name=item.name,
                    label=item.label,
                    kind=item.kind,
                    required=item.required,
                    help=item.help,
                    placeholder=item.placeholder,
                )
                for item in recipe.fields
            ],
            vendors=[_vendor_read(vendor) for vendor in service.vendors_for(recipe)],
        )
        for recipe in service.RECIPES
    ]


@router.post("/config-recipes/{name}", response_model=RenderRead)
def render_recipe(name: str, payload: RenderRequest) -> RenderRead:
    """値から Config を組み立てて返す。保存も投入もしない。"""
    try:
        vendor = Vendor(payload.vendor)
    except ValueError as error:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"知らない機種です: {payload.vendor}"
        ) from error

    try:
        result = service.render(name, vendor, payload.values)
    except service.RecipeError as error:
        # 値の間違いも、出せない機種も、人が直せる話なので同じ扱いにする
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    return RenderRead(
        vendor=str(result.vendor),
        vendor_label=configparse.VENDOR_LABELS.get(result.vendor, str(result.vendor)),
        body=result.body,
        notes=result.notes,
    )
