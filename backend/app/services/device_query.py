"""機器の絞り込み条件。一覧APIとエクスポートAPIで同じ条件を使えるようにまとめている。"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, DeviceType
from app.services.lifecycle import Severity, evaluate


@dataclass
class DeviceFilters:
    q: str | None = None
    vendor: str | None = None
    device_type: DeviceType | None = None
    status: DeviceStatus | None = None
    site_id: int | None = None
    severity: Severity | None = None


def query_devices(db: Session, filters: DeviceFilters) -> list[Device]:
    stmt = select(Device).options(selectinload(Device.contracts), selectinload(Device.site))

    if filters.q:
        like = f"%{filters.q}%"
        stmt = stmt.where(
            or_(
                Device.hostname.ilike(like),
                Device.model.ilike(like),
                Device.serial_number.ilike(like),
            )
        )
    if filters.vendor:
        stmt = stmt.where(Device.vendor == filters.vendor)
    if filters.device_type:
        stmt = stmt.where(Device.device_type == filters.device_type)
    if filters.status:
        stmt = stmt.where(Device.status == filters.status)
    if filters.site_id is not None:
        stmt = stmt.where(Device.site_id == filters.site_id)

    devices = list(db.scalars(stmt.order_by(Device.hostname)))

    # 重要度は日付から算出するため SQL では絞れない。
    # 想定規模（〜数千台）ではアプリ側で判定して問題ない。
    if filters.severity is not None:
        devices = [d for d in devices if evaluate(d).overall_severity == filters.severity]

    return devices
