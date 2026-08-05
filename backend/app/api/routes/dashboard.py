from collections import Counter
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.deps import to_device_read
from app.db import get_db
from app.models import Device, DeviceStatus
from app.schemas import DashboardSummary, DeviceRead, SeverityCounts
from app.schemas.summary import VendorCount
from app.services.lifecycle import Severity, evaluate

router = APIRouter(tags=["dashboard"])

DbSession = Annotated[Session, Depends(get_db)]

_ALERT_SEVERITIES = {Severity.EXPIRED, Severity.CRITICAL, Severity.WARNING}


def _all_devices(db: Session) -> list[Device]:
    stmt = select(Device).options(selectinload(Device.contracts), selectinload(Device.site))
    return list(db.scalars(stmt))


def _counts(values: list[Severity]) -> SeverityCounts:
    c = Counter(v.value for v in values)
    return SeverityCounts(**{k: c.get(k, 0) for k in SeverityCounts.model_fields})


@router.get("/summary", response_model=DashboardSummary)
def summary(db: DbSession, upcoming_limit: int = Query(default=10, le=100)) -> DashboardSummary:
    devices = _all_devices(db)
    statuses = {d.id: evaluate(d) for d in devices}

    alerting = [d for d in devices if statuses[d.id].overall_severity in _ALERT_SEVERITIES]
    # 期限が最も近い順（保守切れ / EoL のうち早い方）
    alerting.sort(key=lambda d: _soonest(statuses[d.id]))

    vendor_counts = Counter(d.vendor for d in devices)

    return DashboardSummary(
        total_devices=len(devices),
        active_devices=sum(1 for d in devices if d.status == DeviceStatus.ACTIVE),
        by_overall_severity=_counts([s.overall_severity for s in statuses.values()]),
        by_maintenance_severity=_counts([s.maintenance_severity for s in statuses.values()]),
        by_eol_severity=_counts([s.eol_severity for s in statuses.values()]),
        by_vendor=[VendorCount(vendor=v, count=n) for v, n in vendor_counts.most_common()],
        devices_without_lifecycle_data=sum(
            1 for d in devices if statuses[d.id].maintenance_end is None and d.eol_date is None
        ),
        upcoming=[to_device_read(d) for d in alerting[:upcoming_limit]],
    )


def _soonest(status) -> int:
    candidates = [d for d in (status.days_to_maintenance_end, status.days_to_eol) if d is not None]
    return min(candidates) if candidates else 10**6


@router.get("/alerts", response_model=list[DeviceRead])
def alerts(
    db: DbSession,
    within_days: int = Query(default=365, ge=0, description="この日数以内に期限を迎える機器"),
) -> list[DeviceRead]:
    """更改計画用: 指定期間内に保守切れ / EoL を迎える機器を期限が近い順に返す。"""
    devices = _all_devices(db)
    result = []
    for device in devices:
        if device.status == DeviceStatus.RETIRED:
            continue
        status = evaluate(device)
        soonest = _soonest(status)
        if soonest <= within_days:
            result.append((soonest, device))
    result.sort(key=lambda pair: pair[0])
    return [to_device_read(d) for _, d in result]
