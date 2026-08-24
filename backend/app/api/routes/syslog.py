"""機器のログを読む口。

外へ通信しないので、機器から集めには行かない。人が集めて放り込む。
障害を追うときは、たいてい手元にログの塊がある。

台帳には何も書かない。読んで数えるだけ。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services import syslog as service

router = APIRouter(tags=["syslog"])

DbSession = Annotated[Session, Depends(get_db)]


class PatternRead(BaseModel):
    code: str
    count: int
    severity: int | None
    severity_label: str
    meaning: str
    hosts: list[str]
    first_at: datetime | None
    last_at: datetime | None
    sample: str


class LogFindingRead(BaseModel):
    """数えた先の判断。件数だけでは現場は動けない。"""

    level: str
    kind: str
    title: str
    why: str
    what_now: str
    count: int
    hosts: list[str]
    first_at: datetime | None
    last_at: datetime | None
    evidence: list[str]


class HostCountRead(BaseModel):
    host: str
    count: int
    heavy: int


class LogReportRead(BaseModel):
    lines: int
    parsed: int
    unreadable: int
    first_at: datetime | None
    last_at: datetime | None
    by_severity: dict[str, int]
    by_host: list[HostCountRead]
    patterns: list[PatternRead]
    known_hosts: list[str]
    unknown_hosts: list[str]
    notes: list[str]
    findings: list[LogFindingRead]


class LogBody(BaseModel):
    text: str


def _read(report: service.LogReport) -> LogReportRead:
    return LogReportRead(
        lines=report.lines,
        parsed=report.parsed,
        unreadable=report.unreadable,
        first_at=report.first_at,
        last_at=report.last_at,
        by_severity=report.by_severity,
        by_host=[HostCountRead(**item) for item in report.by_host],
        patterns=[
            PatternRead(
                code=pattern.code,
                count=pattern.count,
                severity=pattern.severity,
                severity_label=service.SEVERITY_LABELS.get(pattern.severity, "不明")
                if pattern.severity is not None
                else "不明",
                meaning=pattern.meaning,
                hosts=pattern.hosts,
                first_at=pattern.first_at,
                last_at=pattern.last_at,
                sample=pattern.sample,
            )
            for pattern in report.patterns
        ],
        known_hosts=report.known_hosts,
        unknown_hosts=report.unknown_hosts,
        notes=report.notes,
        findings=[
            LogFindingRead(
                level=finding.level,
                kind=finding.kind,
                title=finding.title,
                why=finding.why,
                what_now=finding.what_now,
                count=finding.count,
                hosts=finding.hosts,
                first_at=finding.first_at,
                last_at=finding.last_at,
                evidence=finding.evidence,
            )
            for finding in report.findings
        ],
    )


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp932"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


@router.post("/syslog", response_model=LogReportRead)
def read_text(payload: LogBody, db: DbSession) -> LogReportRead:
    """貼り付けたログを読む。台帳には何も書かない。"""
    if not payload.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "中身が空です")
    return _read(service.review(db, payload.text))


@router.post("/syslog/upload", response_model=LogReportRead)
async def read_files(
    db: DbSession,
    files: list[UploadFile] = File(description="ログのファイル（複数可）"),
) -> LogReportRead:
    """放り込んだログを読む。複数のファイルはまとめて1つとして数える。

    1台ぶんずつ分かれていても、障害を追うときに見たいのは全体の並びなので、
    つなげて数えたほうが役に立つ。
    """
    limit = settings.max_import_bytes
    chunks: list[str] = []
    for file in files:
        raw = await file.read()
        if len(raw) > limit:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"{file.filename} が大きすぎます（上限 {max(1, limit // (1024 * 1024))}MB）",
            )
        chunks.append(_decode(raw))

    text = "\n".join(chunks)
    if not text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "読める中身がありませんでした")
    return _read(service.review(db, text))
