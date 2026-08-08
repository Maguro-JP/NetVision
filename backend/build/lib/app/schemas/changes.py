from datetime import datetime

from pydantic import BaseModel

from app.models import ChangeAction, ChangeSource


class ChangeLogRead(BaseModel):
    id: int
    entity_type: str
    entity_id: int | None
    entity_label: str
    action: ChangeAction
    field: str | None
    field_label: str | None
    old_value: str | None
    new_value: str | None
    source: ChangeSource
    actor: str | None
    batch_id: str | None
    note: str | None
    created_at: datetime


class ChangeLogPage(BaseModel):
    total: int
    items: list[ChangeLogRead]
