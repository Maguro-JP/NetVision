from pydantic import BaseModel, ConfigDict, Field, field_validator


def _blank_to_none(value: str | None) -> str | None:
    """空や空白だけのIDは未設定にする。ユニーク制約で空どうしがぶつかるのを防ぐ。"""
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class SiteBase(BaseModel):
    #: 人が決める拠点ID。プロジェクトで使っている番号があれば入れる
    code: str | None = Field(default=None, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    address: str | None = None
    notes: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _normalize_code(cls, value: object) -> str | None:
        if value is None:
            return None
        return _blank_to_none(str(value))


class SiteCreate(SiteBase):
    pass


class SiteUpdate(BaseModel):
    code: str | None = Field(default=None, max_length=80)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = None
    notes: str | None = None

    @field_validator("code", mode="before")
    @classmethod
    def _normalize_code(cls, value: object) -> str | None:
        if value is None:
            return None
        return _blank_to_none(str(value))


class SiteRead(SiteBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_count: int = 0
