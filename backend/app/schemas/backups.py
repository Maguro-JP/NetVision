"""控えの状態。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BackupRead(BaseModel):
    id: str
    path: str
    created_at: str
    reason: str | None = None
    bytes: int = 0
    exists: bool = True
    #: 開いて確かめたか。None は確かめていない（古い版で取ったもの）
    verified: bool | None = None
    verify_message: str = ""
    #: 表の更新前に取ったもの。戻す先なので自動では消さない
    before_schema_change: bool = False


class BackupStatusRead(BaseModel):
    interval_hours: int = Field(description="何時間おきに取るか。0 は自動で取らない")
    keep: int = Field(description="何世代残すか")
    running: bool
    next_run_at: datetime | None
    latest: BackupRead | None
    backups: list[BackupRead]
    restore_hint: str
