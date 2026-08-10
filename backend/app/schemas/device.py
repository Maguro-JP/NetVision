from datetime import date, datetime

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
    effective_eos_date: date | None
    effective_eol_date: date | None
    # "device"（機器個別の入力） / "product"（型番の製品マスタ） / None（未登録）
    eol_source: str | None
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

    # Config から起こした機器は、読み取れなかった項目が空のまま載る。
    # 入力時は必須のままにしておきたいので、読み出し側だけ空を許す。
    vendor: str = ""
    model: str = ""

    #: 埋まっていない項目の名前。一覧で「情報不足」として見せる
    missing_fields: list[str] = []
    #: 台帳に載った日時。取り込んだばかりの機器を上に出すのに使う
    created_at: datetime | None = None


class DeviceDetail(DeviceRead):
    contracts: list[MaintenanceContractRead] = []
