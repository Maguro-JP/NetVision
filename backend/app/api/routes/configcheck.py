"""Config を放り込んで、その場で見てもらう口。

台帳に入れる前に「どこか変じゃないか」を知りたい場面のほうが多い。
ここでは台帳に何も書かない。見るだけの口が書き込むと、確かめるつもりが
増やすことになる。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services import configcheck as service

router = APIRouter(tags=["config-check"])

DbSession = Annotated[Session, Depends(get_db)]


class FindingRead(BaseModel):
    level: str
    title: str
    why: str
    evidence: list[str]


class CheckRead(BaseModel):
    filename: str
    vendor: str
    vendor_label: str
    hostname: str | None
    model: str | None
    os_version: str | None
    interfaces: int
    vlans: int
    routes: int
    features: list[dict]
    findings: list[FindingRead]
    #: 覚えた流儀と違うところ。間違いとは言い切れないので分けて返す
    differences: list[FindingRead]
    notes: list[str]


class CheckBody(BaseModel):
    text: str
    filename: str = "config.txt"


def _read(result: service.CheckResult) -> CheckRead:
    def finding(item) -> FindingRead:  # noqa: ANN001
        return FindingRead(level=item.level, title=item.title, why=item.why, evidence=item.evidence)

    return CheckRead(
        filename=result.filename,
        vendor=result.vendor,
        vendor_label=result.vendor_label,
        hostname=result.hostname,
        model=result.model,
        os_version=result.os_version,
        interfaces=result.interfaces,
        vlans=result.vlans,
        routes=result.routes,
        features=result.features,
        findings=[finding(item) for item in result.findings],
        differences=[finding(item) for item in result.differences],
        notes=result.notes,
    )


def _decode(raw: bytes) -> str:
    """文字コードを当てる。現場の Config は Shift_JIS のこともある。"""
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@router.post("/config-check", response_model=CheckRead)
def check_text(payload: CheckBody, db: DbSession) -> CheckRead:
    """貼り付けた Config を見る。台帳には何も書かない。"""
    if not payload.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "中身が空です")
    return _read(service.check(db, payload.text, filename=payload.filename))


@router.post("/config-check/upload", response_model=list[CheckRead])
async def check_files(
    db: DbSession,
    files: list[UploadFile] = File(description="running-config などのファイル"),
) -> list[CheckRead]:
    """放り込んだファイルを見る。何個でも受ける。台帳には何も書かない。"""
    limit = settings.max_import_bytes
    out: list[CheckRead] = []
    for file in files:
        raw = await file.read()
        if len(raw) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{file.filename} が大きすぎます（上限 {max(1, limit // (1024 * 1024))}MB）",
            )
        text = _decode(raw)
        if not text.strip():
            continue
        out.append(_read(service.check(db, text, filename=file.filename or "config.txt")))
    if not out:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "読める中身がありませんでした")
    return out
