"""通り道を1本走らせる口。

人 → AI → ロジック → AI → ロジック → AI → 人。どの段が何をしたかを一緒に返す。
どこで決まった値なのかが分からないと、出てきたものを確かめようがない。

生成するだけで、実機には投入しない。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Device
from app.services import pipeline as service

router = APIRouter(tags=["pipeline"])

DbSession = Annotated[Session, Depends(get_db)]


class StageRead(BaseModel):
    name: str
    #: ai / logic / human
    kind: str
    summary: str
    #: 飛ばした理由。動いた段では空
    skipped: str = ""
    model: str | None = None


class DecidedRead(BaseModel):
    key: str
    value: str
    why: str


class GapRead(BaseModel):
    key: str
    question: str
    why: str


class RunRead(BaseModel):
    request: str
    understood: str
    body: str
    decided: list[DecidedRead]
    gaps: list[GapRead]
    risks: list[str]
    findings: list[str]
    ungrounded: list[str]
    explanation: str
    stages: list[StageRead]
    notes: list[str]


class RunBody(BaseModel):
    request: str
    base: str | None = None


@router.post("/devices/{device_id}/pipeline", response_model=RunRead)
def run_pipeline(device_id: int, payload: RunBody, db: DbSession) -> service.Run:
    """やりたいことを渡すと、通り道を1本通して下書きと説明を返す。"""
    device = db.get(Device, device_id)
    if device is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "対象の機器が見つかりません")
    if not payload.request.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "やりたいことを書いてください")
    return service.run(db, device, payload.request, base=payload.base)
