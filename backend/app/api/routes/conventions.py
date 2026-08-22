"""覚えた現場の流儀を返す口。

覚えたことは必ず見せる。台数と割合まで出して、人が見て違えば直せるように
する。裏で勝手に学んで、裏で勝手に使うのがいちばん困る。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import conventions as service

router = APIRouter(tags=["conventions"])

DbSession = Annotated[Session, Depends(get_db)]


class LearnedRead(BaseModel):
    topic: str
    value: str
    seen: int
    total: int
    share: float
    detail: str


class ConventionsRead(BaseModel):
    devices_seen: int
    vlan_step: int | None
    hostname_pattern: str | None
    syslog_hosts: list[str]
    ntp_hosts: list[str]
    stp_mode: str | None
    baseline_lines: list[str]
    learned: list[LearnedRead]


@router.get("/conventions", response_model=ConventionsRead)
def read_conventions(db: DbSession) -> ConventionsRead:
    """取り込んである Config から覚えた、その現場の流儀。

    数えているだけで、揃っていないものは覚えない。半々に割れているものを
    流儀と呼ぶと、どちらでもない現場に片方を押し付けることになる。
    """
    house = service.learn(db)
    return ConventionsRead(
        devices_seen=house.devices_seen,
        vlan_step=house.vlan_step,
        hostname_pattern=house.hostname_pattern,
        syslog_hosts=house.syslog_hosts,
        ntp_hosts=house.ntp_hosts,
        stp_mode=house.stp_mode,
        baseline_lines=house.baseline_lines,
        learned=[
            LearnedRead(
                topic=item.topic,
                value=item.value,
                seen=item.seen,
                total=item.total,
                share=round(item.share, 2),
                detail=item.detail,
            )
            for item in house.learned
        ],
    )
