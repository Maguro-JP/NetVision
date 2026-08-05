from datetime import date

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.device import DeviceStatus, DeviceType
from app.schemas.maintenance import MaintenanceContractRead
from app.services.lifecycle import Severity


class DeviceBase(BaseModel):
    hostname: str = Field(min_length=1, max_length=120)
    vendor: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    serial_number: str | None = None
    device_type: DeviceType = DeviceType.OTHER
    status: DeviceStatus = DeviceStatus.ACTIVE

    os_name: str | None = None
    os_version: str | None = None
    firmware_version: str | None = None

    site_id: int | None = None
    rack: str | None = None
    rack_unit: str | None = None
    mgmt_ip: str | None = None

    install_date: date | None = None
    eos_date: date | None = None
    eol_date: date | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def _check_lifecycle_order(self):
        if self.eos_date and self.eol_date and self.eol_date < self.eos_date:
            raise ValueError(
                "eol_date（サポート終了）は eos_date（販売終了）以降である必要があります"
            )
        return self


class DeviceCreate(DeviceBase):
    pass


class DeviceUpdate(BaseModel):
    """PATCH 用。未指定のフィールドは変更しない。"""

    model_config = ConfigDict(extra="forbid")

    hostname: str | None = Field(default=None, min_length=1, max_length=120)
    vendor: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    serial_number: str | None = None
    device_type: DeviceType | None = None
    status: DeviceStatus | None = None

    os_name: str | None = None
    os_version: str | None = None
    firmware_version: str | None = None

    site_id: int | None = None
    rack: str | None = None
    rack_unit: str | None = None
    mgmt_ip: str | None = None

    install_date: date | None = None
    eos_date: date | None = None
    eol_date: date | None = None
    notes: str | None = None


class LifecycleStatusRead(BaseModel):
    maintenance_end: date | None
    days_to_maintenance_end: int | None
    maintenance_severity: Severity
    days_to_eos: int | None
    eos_severity: Severity
    days_to_eol: int | None
    eol_severity: Severity
    overall_severity: Severity
    age_years: float | None


class DeviceRead(DeviceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    site_name: str | None = None
    lifecycle: LifecycleStatusRead


class DeviceDetail(DeviceRead):
    contracts: list[MaintenanceContractRead] = []
