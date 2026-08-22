"""何にどこまで対応しているかを返す口。

「これは対応していますか」に口で答えると、そのたびに言い方が変わる。
できることと、できないことを同じ場所から返す。
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.services import capabilities as service

router = APIRouter(tags=["capabilities"])


class CapabilityRead(BaseModel):
    area: str
    name: str
    read: str
    review: str
    generate: str
    note: str


class CapabilityIndex(BaseModel):
    areas: list[str]
    levels: dict[str, str]
    items: list[CapabilityRead]


@router.get("/capabilities", response_model=CapabilityIndex)
def list_capabilities() -> CapabilityIndex:
    """対応状況の一覧。段階は 読む / 検算 / 起こす の3つに分ける。"""
    return CapabilityIndex(
        areas=service.areas(),
        levels=service.LEVEL_LABELS,
        items=[
            CapabilityRead(
                area=item.area,
                name=item.name,
                read=item.read,
                review=item.review,
                generate=item.generate,
                note=item.note,
            )
            for item in service.CAPABILITIES
        ],
    )
