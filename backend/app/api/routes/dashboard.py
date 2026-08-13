from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import to_device_read
from app.db import get_db
from app.models import DeviceStatus
from app.schemas import DashboardSummary, DeviceRead, SeverityCounts
from app.schemas.summary import VendorCount
from app.services.device_query import (
    DeviceFilters,
    count_by_eol_severity,
    count_by_maintenance_severity,
    count_by_overall_severity,
    count_by_vendor,
    count_devices,
    count_without_lifecycle_data,
    query_alerts,
    query_upcoming,
)

router = APIRouter(tags=["dashboard"])

DbSession = Annotated[Session, Depends(get_db)]


def _counts(values: dict[str, int]) -> SeverityCounts:
    return SeverityCounts(**values)


@router.get("/summary", response_model=DashboardSummary)
def summary(db: DbSession, upcoming_limit: int = Query(default=10, le=100)) -> DashboardSummary:
    """トップ画面用の集計。件数は SQL で出し、機器の中身は期限が近い分だけ読む。

    以前は全機器を ORM 化して1台ずつ判定していた。16万台では読み込みだけで
    10秒を超え、最初の画面で固まっていた。
    """
    return DashboardSummary(
        total_devices=count_devices(db, DeviceFilters()),
        active_devices=count_devices(db, DeviceFilters(status=DeviceStatus.ACTIVE)),
        by_overall_severity=_counts(count_by_overall_severity(db)),
        by_maintenance_severity=_counts(count_by_maintenance_severity(db)),
        by_eol_severity=_counts(count_by_eol_severity(db)),
        by_vendor=[VendorCount(vendor=vendor, count=n) for vendor, n in count_by_vendor(db)],
        devices_without_lifecycle_data=count_without_lifecycle_data(db),
        upcoming=[to_device_read(d) for d in query_upcoming(db, upcoming_limit)],
    )


@router.get("/alerts", response_model=list[DeviceRead])
def alerts(
    db: DbSession,
    within_days: int = Query(default=365, ge=0, description="この日数以内に期限を迎える機器"),
) -> list[DeviceRead]:
    """更改計画用: 指定期間内に保守切れ / EoL を迎える機器を期限が近い順に返す。"""
    return [to_device_read(d) for d in query_alerts(db, within_days)]
