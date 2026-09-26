from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ChangeAction, ChangeLog, ChangeSource
from app.schemas.changes import ChangeLogPage, ChangeLogRead
from app.services import revert
from app.services.audit import audit_context
from app.services.importer import FIELD_LABELS

router = APIRouter(prefix="/changes", tags=["changes"])

DbSession = Annotated[Session, Depends(get_db)]


def _to_read(log: ChangeLog) -> ChangeLogRead:
    return ChangeLogRead(
        id=log.id,
        entity_type=log.entity_type,
        entity_id=log.entity_id,
        entity_label=log.entity_label,
        action=log.action,
        field=log.field,
        field_label=FIELD_LABELS.get(log.field or "", log.field),
        old_value=log.old_value,
        new_value=log.new_value,
        source=log.source,
        actor=log.actor,
        batch_id=log.batch_id,
        note=log.note,
        created_at=log.created_at,
    )


@router.get("", response_model=ChangeLogPage)
def list_changes(
    db: DbSession,
    entity_type: str | None = Query(default=None, description="device / contract / site"),
    entity_id: int | None = None,
    action: ChangeAction | None = None,
    source: ChangeSource | None = None,
    batch_id: str | None = None,
    q: str | None = Query(default=None, description="対象名 / 変更内容の部分一致"),
    since: datetime | None = Query(default=None, description="この日時以降の変更"),
    until: datetime | None = None,
    limit: int = Query(default=100, le=1000),
    offset: int = 0,
) -> ChangeLogPage:
    """構成変更の履歴を新しい順に返す。"""
    stmt = select(ChangeLog)

    if entity_type:
        stmt = stmt.where(ChangeLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(ChangeLog.entity_id == entity_id)
    if action:
        stmt = stmt.where(ChangeLog.action == action)
    if source:
        stmt = stmt.where(ChangeLog.source == source)
    if batch_id:
        stmt = stmt.where(ChangeLog.batch_id == batch_id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(
            or_(
                ChangeLog.entity_label.ilike(like),
                ChangeLog.old_value.ilike(like),
                ChangeLog.new_value.ilike(like),
            )
        )
    if since:
        stmt = stmt.where(ChangeLog.created_at >= since)
    if until:
        stmt = stmt.where(ChangeLog.created_at <= until)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    logs = db.scalars(
        stmt.order_by(ChangeLog.created_at.desc(), ChangeLog.id.desc()).limit(limit).offset(offset)
    )
    return ChangeLogPage(total=total, items=[_to_read(log) for log in logs])


class RevertBatchResult(BaseModel):
    reverted: int
    skipped: list[dict[str, object]]


@router.post("/{entry_id}/revert", response_model=ChangeLogRead)
def revert_change(entry_id: int, db: DbSession) -> ChangeLogRead:
    """変更履歴1件を戻す。

    手で台帳を直す作業は、戻せないと踏み切れない。戻せない履歴（作成・削除や
    対象が消えている場合）は、なぜ戻せないかを返す。

    戻した操作そのものも履歴に残る。戻したことを消さないため。
    """
    with audit_context(ChangeSource.UI, note=f"変更履歴 {entry_id} を戻す"):
        try:
            revert.revert_entry(db, entry_id)
        except revert.NotRevertable as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    entry = db.get(ChangeLog, entry_id)
    return _to_read(entry)  # type: ignore[arg-type]


@router.post("/batches/{batch_id}/revert", response_model=RevertBatchResult)
def revert_batch(batch_id: str, db: DbSession) -> RevertBatchResult:
    """取り込み1回分をまとめて戻す。

    台帳を丸ごと取り込んで結果がおかしかったとき、1件ずつ戻すのは現実的ではない。
    戻せなかった履歴は理由を添えて返すので、残りは手で判断できる。
    """
    with audit_context(ChangeSource.UI, note=f"取り込み {batch_id} を戻す"):
        try:
            result = revert.revert_batch(db, batch_id)
        except revert.NotRevertable as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    return RevertBatchResult(
        reverted=result.reverted,
        skipped=[{"id": entry_id, "reason": reason} for entry_id, reason in result.skipped],
    )
