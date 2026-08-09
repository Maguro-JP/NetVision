"""構成変更の履歴を自動で記録する。

個々のAPIに記録処理を書くと必ず抜けが出るので、SQLAlchemy のフラッシュに介入して
**1か所で全ての変更を捕捉する**方式にしている。画面からの編集も台帳の取り込みも、
同じ経路で履歴が残る。

- before_flush: 変更前後の値を拾う（フラッシュ後は差分が取れないため）
- after_flush : 採番済みのIDを添えて履歴を書き込む
"""

from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime

from sqlalchemy import event, insert, inspect
from sqlalchemy.orm import Session

from app.models import Device, MaintenanceContract, Site
from app.models.change_log import ChangeAction, ChangeLog, ChangeSource

# 履歴を取らない項目（自動更新されるだけで意味を持たない）
IGNORED_FIELDS = frozenset({"id", "created_at", "updated_at"})

_PENDING_KEY = "_netvision_audit_pending"


@dataclass
class AuditContext:
    source: ChangeSource = ChangeSource.UI
    actor: str | None = None
    batch_id: str | None = None
    note: str | None = None


_DEFAULT_CONTEXT = AuditContext()
_context: ContextVar[AuditContext | None] = ContextVar("audit_context", default=None)


@contextmanager
def audit_context(
    source: ChangeSource,
    *,
    actor: str | None = None,
    batch_id: str | None = None,
    note: str | None = None,
):
    """この中で起きた変更に、発生元・実行者・バッチIDを付ける。"""
    token = _context.set(
        AuditContext(
            source=source, actor=actor, batch_id=batch_id or uuid.uuid4().hex[:12], note=note
        )
    )
    try:
        yield
    finally:
        _context.reset(token)


# ---------------------------------------------------------------------------
# 対象の識別
# ---------------------------------------------------------------------------


def _describe(obj: object, session: Session) -> tuple[str, str] | None:
    """(種類, 表示名) を返す。履歴の対象外なら None。"""
    if isinstance(obj, Device):
        return "device", obj.hostname or "(名称未設定)"
    if isinstance(obj, Site):
        return "site", obj.name or "(名称未設定)"
    if isinstance(obj, MaintenanceContract):
        # 契約単体では何の契約か分からないので、必ず機器名を添える。
        # 多くの場合は識別マップから取れるため、追加のクエリは走らない。
        device = session.get(Device, obj.device_id) if obj.device_id else None
        name = obj.contract_number or obj.provider or "(契約)"
        return "contract", f"{device.hostname} / {name}" if device else str(name)
    return None


def _format(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        from app.services import localtime

        return localtime.format(value)
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    return text if text != "" else None


@dataclass
class _PendingChange:
    entity_type: str
    entity_label: str
    action: ChangeAction
    field: str | None = None
    old_value: str | None = None
    new_value: str | None = None
    entity_id: int | None = None
    # 新規作成はフラッシュ後でないとIDが決まらないので、オブジェクトを持っておく
    source_object: object | None = dataclass_field(default=None, repr=False)


def _changed_fields(obj: object) -> list[tuple[str, object, object]]:
    """更新された項目の (項目名, 変更前, 変更後) を返す。"""
    state = inspect(obj)
    changes: list[tuple[str, object, object]] = []
    for attr in state.mapper.column_attrs:
        if attr.key in IGNORED_FIELDS:
            continue
        history = state.attrs[attr.key].history
        if not history.has_changes():
            continue
        old = history.deleted[0] if history.deleted else None
        new = history.added[0] if history.added else None
        if old == new:
            continue
        changes.append((attr.key, old, new))
    return changes


def _snapshot(obj: object) -> list[tuple[str, object]]:
    state = inspect(obj)
    return [
        (attr.key, getattr(obj, attr.key, None))
        for attr in state.mapper.column_attrs
        if attr.key not in IGNORED_FIELDS
    ]


@event.listens_for(Session, "before_flush")
def _capture_changes(session: Session, flush_context, instances) -> None:  # noqa: ARG001
    pending: list[_PendingChange] = session.info.setdefault(_PENDING_KEY, [])

    for obj in session.new:
        described = _describe(obj, session)
        if described is None:
            continue
        entity_type, label = described
        # 新規は「作成」1件にまとめ、初期値は note ではなく個別項目として残さない
        pending.append(
            _PendingChange(
                entity_type=entity_type,
                entity_label=label,
                action=ChangeAction.CREATE,
                new_value=_summary(obj),
                source_object=obj,
            )
        )

    for obj in session.dirty:
        described = _describe(obj, session)
        if described is None or not session.is_modified(obj, include_collections=False):
            continue
        entity_type, label = described
        for name, old, new in _changed_fields(obj):
            pending.append(
                _PendingChange(
                    entity_type=entity_type,
                    entity_label=label,
                    action=ChangeAction.UPDATE,
                    field=name,
                    old_value=_format(old),
                    new_value=_format(new),
                    entity_id=getattr(obj, "id", None),
                )
            )

    for obj in session.deleted:
        described = _describe(obj, session)
        if described is None:
            continue
        entity_type, label = described
        pending.append(
            _PendingChange(
                entity_type=entity_type,
                entity_label=label,
                action=ChangeAction.DELETE,
                old_value=_summary(obj),
                entity_id=getattr(obj, "id", None),
            )
        )


def _summary(obj: object) -> str:
    """作成/削除の記録用に、主要な値を1行にまとめる。"""
    parts = [f"{key}={_format(value)}" for key, value in _snapshot(obj) if value not in (None, "")]
    text = ", ".join(parts)
    return text[:2000]


@event.listens_for(Session, "after_flush")
def _write_changes(session: Session, flush_context) -> None:  # noqa: ARG001
    pending: list[_PendingChange] = session.info.pop(_PENDING_KEY, [])
    if not pending:
        return

    ctx = _context.get() or _DEFAULT_CONTEXT
    rows = []
    for change in pending:
        entity_id = change.entity_id
        if entity_id is None and change.source_object is not None:
            entity_id = getattr(change.source_object, "id", None)
        rows.append(
            {
                "entity_type": change.entity_type,
                "entity_id": entity_id,
                "entity_label": change.entity_label,
                "action": change.action,
                "field": change.field,
                "old_value": change.old_value,
                "new_value": change.new_value,
                "source": ctx.source,
                "actor": ctx.actor,
                "batch_id": ctx.batch_id,
                "note": ctx.note,
            }
        )

    # ORM を経由すると再帰的にフラッシュが走るため、Core の INSERT で直接書き込む
    session.execute(insert(ChangeLog), rows)
