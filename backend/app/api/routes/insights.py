from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.insights import InsightRead, InsightReportRead
from app.services.device_query import DeviceFilters, query_devices
from app.services.insights import InsightCategory, analyze
from app.services.reference import load_vulnerabilities

router = APIRouter(prefix="/insights", tags=["insights"])

DbSession = Annotated[Session, Depends(get_db)]


@router.get("", response_model=InsightReportRead)
def list_insights(
    db: DbSession,
    category: InsightCategory | None = Query(
        default=None, description="cost / risk / data / consolidation"
    ),
    site_id: int | None = None,
) -> InsightReportRead:
    """台帳から読み取れる「無駄」と「リスク」を洗い出す。

    削減できる年額の見込みも併せて返すので、更改や契約見直しの根拠に使える。
    """
    devices = query_devices(db, DeviceFilters(site_id=site_id))
    report = analyze(devices, vulnerabilities=load_vulnerabilities(db))

    if category is not None:
        filtered = [i for i in report.insights if i.category is category]
        return InsightReportRead(
            total=len(filtered),
            annual_saving_total=sum(i.annual_saving or 0 for i in filtered),
            by_category=report.by_category,
            insights=[InsightRead.model_validate(i, from_attributes=True) for i in filtered],
        )

    return InsightReportRead.model_validate(report, from_attributes=True)
