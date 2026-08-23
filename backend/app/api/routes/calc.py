"""数えれば分かることを返す口。

推測は入らない。台帳と、Config から落とした観測値を数えているだけなので、
同じ入力なら何度でも同じ答えになる。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import calc as service

router = APIRouter(tags=["calc"])

DbSession = Annotated[Session, Depends(get_db)]


class SubnetUsageRead(BaseModel):
    subnet: str
    capacity: int
    used: int
    free: int
    ratio: float
    crowded: bool
    vlan_id: int | None
    vlan_name: str | None
    devices: list[str]
    basis: str


class VlanNumbersRead(BaseModel):
    used: list[int]
    free_next: list[int]
    used_count: int
    basis: str


class PortUsageRead(BaseModel):
    device_id: int
    hostname: str
    total: int
    in_use: int
    free: int
    ratio: float
    basis: str


class LagUsageRead(BaseModel):
    device_id: int
    hostname: str
    group: int
    members: int
    total_mbps: int | None
    remaining_mbps: int | None
    note: str


class DuplicateAddressRead(BaseModel):
    address: str
    places: list[str]


class CalcReportRead(BaseModel):
    subnets: list[SubnetUsageRead]
    vlans: VlanNumbersRead | None
    ports: list[PortUsageRead]
    lags: list[LagUsageRead]
    duplicates: list[DuplicateAddressRead]
    notes: list[str]


@router.get("/calc", response_model=CalcReportRead)
def read_calc(db: DbSession) -> service.CalcReport:
    """セグメントの空き・VLAN 番号の空き・ポートの残り・束の帯域・重複アドレス。"""
    return service.report(db)
