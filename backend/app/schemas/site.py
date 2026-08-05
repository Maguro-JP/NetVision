from pydantic import BaseModel, ConfigDict, Field


class SiteBase(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    address: str | None = None
    notes: str | None = None


class SiteCreate(SiteBase):
    pass


class SiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    address: str | None = None
    notes: str | None = None


class SiteRead(SiteBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_count: int = 0
