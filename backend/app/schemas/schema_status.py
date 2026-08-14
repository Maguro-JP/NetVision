from pydantic import BaseModel, Field


class SchemaStepRead(BaseModel):
    revision: str
    summary: str


class SchemaBackupRead(BaseModel):
    id: str
    path: str
    created_at: str
    from_revision: str | None = None
    to_revision: str | None = None
    bytes: int = 0
    documents_bytes: int = 0
    kind: str
    confirmed_at: str | None = None


class SchemaCountsRead(BaseModel):
    devices: int
    sites: int


class SchemaStatusRead(BaseModel):
    """表の版と、控えの状態。

    phase:
    - ready: 通常どおり使える
    - needs_apply: 差分がある。控えを取ってから更新する
    - needs_confirm: 更新済み。問題ないことを確認するまで控えを残す
    """

    phase: str
    current_revision: str | None = None
    head_revision: str
    pending: list[SchemaStepRead] = Field(default_factory=list)
    counts: SchemaCountsRead | None = None
    unconfirmed: list[SchemaBackupRead] = Field(default_factory=list)
