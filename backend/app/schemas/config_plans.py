from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.config_plan import PlanStatus


class ConfigPlanBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    status: PlanStatus = PlanStatus.DRAFT
    body: str = ""
    vendor: str | None = None
    scenario_id: int | None = None
    base_snapshot_id: int | None = None


class ConfigPlanCreate(ConfigPlanBase):
    device_id: int
    # 将来フルの初期値をどこから作るか
    # design = 図に描いた構成から起こす（新規構築・テスト環境向け）
    seed_from: str = Field(default="current", pattern="^(current|template|blank|design)$")
    template_id: int | None = None


class ConfigPlanUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    status: PlanStatus | None = None
    body: str | None = None
    vendor: str | None = None
    scenario_id: int | None = None
    base_snapshot_id: int | None = None


class ConfigPlanRead(ConfigPlanBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
    device_hostname: str | None = None
    revision: int
    created_at: datetime
    updated_at: datetime


class CommandRead(BaseModel):
    text: str
    depth: int
    kind: str  # add（追加） / remove（削除） / context（見出しの再掲）
    rendered: str


class DiffRead(BaseModel):
    """差分の結果。

    `verified` が False のとき `commands` は必ず空になる。
    将来フルと同じ状態になると確認できていないコマンドは渡さない。
    """

    verified: bool
    commands: list[CommandRead]
    script: str
    rollback_script: str
    mismatches: list[str]
    warnings: list[str]
    risks: list[str]
    added: int
    removed: int
    changed: int


class PlanOutputRead(BaseModel):
    plan_id: int
    device_hostname: str | None
    vendor: str
    base_snapshot_id: int | None
    base_is_stale: bool
    notes: list[str]
    current_full: str
    future_full: str
    diff: DiffRead


class DiffPreviewRequest(BaseModel):
    """plan を作らずに、その場で2つの config を比べる。"""

    current: str
    target: str
    vendor: str | None = None
