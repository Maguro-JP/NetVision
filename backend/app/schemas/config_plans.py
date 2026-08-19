from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from app.models.config_plan import PlanStatus
from app.schemas.types import Timestamp


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
    created_at: Timestamp
    updated_at: Timestamp


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


class ConfigDraftRequest(BaseModel):
    """資料を根拠に下書きを頼む。"""

    #: やりたいことを言葉で。「VLAN 120 を作って Gi0/1 に乗せる」など
    request: str
    #: 前提にする config。省くと最新の取り込み済みを使う
    base: str | None = None


class DraftLineRead(BaseModel):
    text: str
    grounded: bool
    risks: list[str]


class DraftSourceRead(BaseModel):
    document_id: int
    title: str
    heading: str | None
    commands: list[str]


class ConfigDraftRead(BaseModel):
    device_id: int
    vendor: str
    body: str
    lines: list[DraftLineRead]
    sources: list[DraftSourceRead]
    risks: list[str]
    notes: list[str]
    #: 下書きを起こしたモデル。資料から拾っただけのときは null
    model: str | None = None


class LlmModelRead(BaseModel):
    role: str
    #: 使えないときの理由。画面にそのまま出す
    note: str = ""
    model: str
    endpoint: str
    local: bool
    usable: bool
    blocked_external: bool


class LlmStatusRead(BaseModel):
    #: 使う設定になっているか（NETVISION_LLM_ENABLED）
    enabled: bool = True
    configured: bool
    models: list[LlmModelRead]


class ConfigReviewRequest(BaseModel):
    """よそで書いた設定を、こちらで検める。"""

    #: 何をしたかったか。資料を引き当てるのに使う
    request: str = ""
    #: 貼り付けた設定
    body: str
