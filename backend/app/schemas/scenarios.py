from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.scenario import ScenarioAction, ScenarioStatus
from app.schemas.types import refuse_null


class ScenarioItemBase(BaseModel):
    action: ScenarioAction
    device_id: int | None = None
    planned_vendor: str | None = None
    planned_model: str | None = None
    quantity: int = Field(default=1, ge=1, le=9999)
    unit_cost: float | None = Field(default=None, ge=0)
    planned_annual_cost: float | None = Field(default=None, ge=0)
    planned_install_date: date | None = None
    planned_eol_date: date | None = None
    notes: str | None = None


class ScenarioItemCreate(ScenarioItemBase):
    pass


class ScenarioItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ScenarioAction | None = None
    device_id: int | None = None
    planned_vendor: str | None = None
    planned_model: str | None = None
    quantity: int | None = Field(default=None, ge=1, le=9999)
    unit_cost: float | None = Field(default=None, ge=0)
    planned_annual_cost: float | None = Field(default=None, ge=0)
    planned_install_date: date | None = None
    planned_eol_date: date | None = None
    notes: str | None = None


class ScenarioItemRead(ScenarioItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    scenario_id: int
    device_label: str | None = None


class ScenarioBase(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    status: ScenarioStatus = ScenarioStatus.DRAFT
    target_date: date | None = None


class ScenarioCreate(ScenarioBase):
    pass


class ScenarioUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = None
    status: ScenarioStatus | None = None
    target_date: date | None = None

    _not_null = field_validator("name", "status", mode="before")(refuse_null)


class ScenarioRead(ScenarioBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_count: int = 0


class ScenarioDetail(ScenarioRead):
    items: list[ScenarioItemRead] = []


class ConfigurationMetricsRead(BaseModel):
    device_count: int
    annual_maintenance_cost: float
    expired_count: int
    at_risk_count: int
    unsupported_count: int
    average_age_years: float | None


class ScenarioSummaryRead(BaseModel):
    scenario_id: int
    name: str
    status: str
    evaluated_on: date
    current: ConfigurationMetricsRead
    projected: ConfigurationMetricsRead
    investment: float
    annual_cost_delta: float
    item_counts: dict[str, int]
    warnings: list[str]
