from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ChangeAction, ChangeLog, ChangeSource
from app.schemas.changes import ChangeLogPage, ChangeLogRead
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
