from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MaintenanceContractBase(BaseModel):
    contract_number: str | None = None
    provider: str = Field(min_length=1, max_length=120)
    service_level: str | None = None
    start_date: date
    end_date: date
    annual_cost: float | None = Field(default=None, ge=0)
    contact: str | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_period(self):
        if self.end_date < self.start_date:
            raise ValueError("end_date は start_date 以降である必要があります")
        return self


class MaintenanceContractCreate(MaintenanceContractBase):
    pass


class MaintenanceContractUpdate(BaseModel):
    contract_number: str | None = None
    provider: str | None = Field(default=None, min_length=1, max_length=120)
    service_level: str | None = None
    start_date: date | None = None
    end_date: date | None = None
    annual_cost: float | None = Field(default=None, ge=0)
    contact: str | None = None
    notes: str | None = None


class MaintenanceContractRead(MaintenanceContractBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_id: int
