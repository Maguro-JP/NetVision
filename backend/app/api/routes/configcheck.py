"""Config を放り込んで、その場で見てもらう口。

台帳に入れる前に「どこか変じゃないか」を知りたい場面のほうが多い。
ここでは台帳に何も書かない。見るだけの口が書き込むと、確かめるつもりが
増やすことになる。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.services import configcheck as service
from app.services import configsplit, textencoding

router = APIRouter(tags=["config-check"])

DbSession = Annotated[Session, Depends(get_db)]


class FindingRead(BaseModel):
    level: str
    title: str
    why: str
    evidence: list[str]
    #: 既定値に拠った指摘の、拠った資料の名前。無ければ空
    sources: list[str] = []


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
        return FindingRead(
            level=item.level,
            title=item.title,
            why=item.why,
            evidence=item.evidence,
            sources=item.sources,
        )

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


#: 文字コードの判別は textencoding にまとめてある。cp932 と EUC-JP は
#: お互いのバイト列をエラー無しに読めるので、順に試すだけでは化ける
_decode = textencoding.decode


@router.post("/config-check", response_model=CheckRead)
def check_text(payload: CheckBody, db: DbSession) -> CheckRead:
    """貼り付けた Config を見る。台帳には何も書かない。"""
    if not payload.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "中身が空です")
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
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"{file.filename} が大きすぎます（上限 {max(1, limit // (1024 * 1024))}MB）",
            )
        text = _decode(raw)
        if not text.strip():
            continue
        # 1つのファイルに何台分も貼ってあることがある。混ぜて見ると、
        # 2台目の口が1台目のものとして指摘に出る
        name = file.filename or "config.txt"
        chunks = configsplit.split(text)
        for number, chunk in enumerate(chunks, start=1):
            label = name if len(chunks) == 1 else f"{name} ({number}/{len(chunks)})"
            out.append(_read(service.check(db, chunk, filename=label)))
    if not out:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "読める中身がありませんでした")
    return out


# ---------------------------------------------------------------------------
# 出した結果について、聞き返す
# ---------------------------------------------------------------------------


class AskBackBody(BaseModel):
    question: str
    #: 何について聞いているか。見た Config をそのまま渡してよい
    context: str = ""


class AskBackRead(BaseModel):
    answer: str
    source: str
    used: list[str]
    prompt: str | None
    note: str


@router.post("/config-check/ask", response_model=AskBackRead)
def ask_back(payload: AskBackBody, db: DbSession) -> AskBackRead:
    """出した結果について聞き返す。

    答え方は3段。用語の説明で足りるならそれを返し、手元にモデルがあれば
    読み取った内容を材料にして答えてもらい、どちらも無ければ承認済みの AI へ
    貼るための依頼文を返す。

    どの段で答えたかは必ず返す。何を見て答えたのかが無い答えは、
    確かめようがない。
    """
    from app.services import askback

    text = payload.context.strip()
    findings = service.check(db, text).findings if text else []
    result = askback.ask(db, payload.question, findings=findings, context=text)
    return AskBackRead(
        answer=result.answer,
        source=result.source,
        used=result.used,
        prompt=result.prompt,
        note=result.note,
    )


# ---------------------------------------------------------------------------
# 選んだところを直してもらう
# ---------------------------------------------------------------------------


class EditBody(BaseModel):
    selection: str = Field(description="直したい範囲（選んだところ）")
    request: str = Field(description="どう直したいか")
    description: str = Field(default="", description="説明に入れる文字（任意）")


class EditRead(BaseModel):
    before: str
    after: str
    diff: str
    changed: bool
    source: str
    notes: list[str]
    prompt: str | None


@router.post("/config-check/edit", response_model=EditRead)
def edit_selection(payload: EditBody) -> EditRead:
    """選んだ範囲だけを直す。範囲の外は触らない。

    全部を作り直すと、どこが変わったのかを人が突き合わせることになり、
    結局読み切れない。差分にして返し、採るかどうかは人が決める。
    """
    from app.services import config_edit

    if not payload.request.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "どう直したいかを書いてください")

    result = config_edit.edit(payload.selection, payload.request, description=payload.description)
    return EditRead(
        before=result.before,
        after=result.after,
        diff=result.diff,
        changed=result.changed,
        source=result.source,
        notes=result.notes,
        prompt=result.prompt,
    )
