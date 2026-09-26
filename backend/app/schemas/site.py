from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models import SiteStatus
from app.schemas.types import refuse_null


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
    #: いまどういう状態か。閉鎖しても機器は残す
    status: SiteStatus = SiteStatus.ACTIVE
    opened_on: date | None = None
    closed_on: date | None = None
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
    status: SiteStatus | None = None
    opened_on: date | None = None
    closed_on: date | None = None
    notes: str | None = None

    _not_null = field_validator("name", "status", mode="before")(refuse_null)

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
