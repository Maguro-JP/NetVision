from pydantic import BaseModel

from app.schemas.device import DeviceRead


class SeverityCounts(BaseModel):
    expired: int = 0
    critical: int = 0
    warning: int = 0
    ok: int = 0
    unknown: int = 0


class VendorCount(BaseModel):
    vendor: str
    count: int


class DashboardSummary(BaseModel):
    """トップ画面用のサマリ。「今どこが危ないか」を1リクエストで返す。"""

    total_devices: int
    active_devices: int
    by_overall_severity: SeverityCounts
    by_maintenance_severity: SeverityCounts
    by_eol_severity: SeverityCounts
    by_vendor: list[VendorCount]
    devices_without_lifecycle_data: int
    upcoming: list[DeviceRead]  # 期限が近い順の要対応機器
