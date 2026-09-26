"""SNMP で取った値と、OS の版の散らばりを返す口。

外へ通信しないので、機器へ取りには行かない。人が snmpwalk で取ってきた
出力を持ち込む。台帳には何も書かない。読んで突き合わせるだけ。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db
from app.services import mib as service
from app.services import osreport

router = APIRouter(tags=["mib"])

DbSession = Annotated[Session, Depends(get_db)]

#: 取り込む出力の上限。1台ぶんの snmpwalk はこの範囲に収まる
MAX_BYTES = 8 * 1024 * 1024


class PortRead(BaseModel):
    index: str
    name: str
    alias: str
    admin: str
    oper: str
    speed_mbps: int | None
    #: 「使う設定です / 落ちています」のような1行
    state: str
    down_but_enabled: bool


class ValueRead(BaseModel):
    key: str
    oid: str
    index: str
    raw: str
    label: str
    plain: str


class MibReportRead(BaseModel):
    lines: int
    read: int
    unknown: int
    hostname: str | None
    descr: str | None
    vendor: str | None
    model: str | None
    os_version: str | None
    serial: str | None
    location: str | None
    uptime: str | None
    ports: list[PortRead]
    values: list[ValueRead]
    differences: list[str]
    notes: list[str]


class MibBody(BaseModel):
    text: str


class RunningRead(BaseModel):
    version: str
    count: int
    hostnames: list[str]
    newest: bool


class ModelSpreadRead(BaseModel):
    model: str
    os_name: str
    devices: int
    versions: list[RunningRead]
    newest: str
    behind: int
    unknown: int
    basis: str
    mixed: bool


class OsReportRead(BaseModel):
    models: list[ModelSpreadRead]
    unknown_total: int
    devices: int
    notes: list[str]


def _read(report: service.MibReport) -> MibReportRead:
    return MibReportRead(
        lines=report.lines,
        read=report.read,
        unknown=report.unknown,
        hostname=report.hostname,
        descr=report.descr,
        vendor=report.vendor,
        model=report.model,
        os_version=report.os_version,
        serial=report.serial,
        location=report.location,
        uptime=report.uptime,
        ports=[
            PortRead(
                index=port.index,
                name=port.name,
                alias=port.alias,
                admin=port.admin,
                oper=port.oper,
                speed_mbps=port.speed_mbps,
                state=service.status_label(port),
                down_but_enabled=port.down_but_enabled,
            )
            for port in report.ports
        ],
        # 生の値は多いので、意味を持っているものだけを画面へ回す
        values=[
            ValueRead(
                key=value.key,
                oid=value.oid,
                index=value.index,
                raw=value.raw,
                label=value.label,
                plain=value.plain,
            )
            for value in report.values
            if value.label
        ][:200],
        differences=report.differences,
        notes=report.notes,
    )


@router.post("/mib", response_model=MibReportRead)
def read_mib(payload: MibBody, db: DbSession) -> MibReportRead:
    """貼り付けた snmpwalk の出力を読む。"""
    if not payload.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "読ませるものがありません")
    return _read(service.review(db, payload.text))


@router.post("/mib/upload", response_model=MibReportRead)
async def upload_mib(db: DbSession, files: list[UploadFile] = File(...)) -> MibReportRead:
    """ファイルで渡された出力を読む。複数あればつないで1つとして数える。"""
    chunks: list[str] = []
    total = 0
    for item in files:
        body = await item.read()
        total += len(body)
        if total > MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "大きすぎます。機器ごとに分けて渡してください",
            )
        chunks.append(body.decode("utf-8", errors="replace"))
    text = "\n".join(chunks)
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "中身がありません")
    return _read(service.review(db, text))


@router.get("/os-report", response_model=OsReportRead)
def read_os_report(db: DbSession) -> osreport.OsReport:
    """機種ごとに、動いている OS の版と、遅れている台数を返す。"""
    return osreport.review(db)
