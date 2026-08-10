from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db import get_db
from app.schemas.insights import InsightRead, InsightReportRead, KindSummaryRead
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
    limit: int = Query(default=200, le=2000, description="返す指摘の件数（重い順）"),
) -> InsightReportRead:
    """台帳から読み取れる「無駄」と「リスク」を洗い出す。

    削減できる年額の見込みも併せて返すので、更改や契約見直しの根拠に使える。

    指摘そのものは重い順に `limit` 件までしか返さない。16万台の環境では
    40万件近くになり、全部返すと応答が 150MB を超えて画面側が固まる。
    件数と削減見込みは絞る前の全件から出しているので、数字は変わらない。
    種類ごとの内訳は `by_kind` を見る。
    """
    devices = query_devices(db, DeviceFilters(site_id=site_id))
    report = analyze(devices, vulnerabilities=load_vulnerabilities(db), keep=limit)

    if category is not None:
        filtered = [i for i in report.insights if i.category is category]
        return InsightReportRead(
            total=report.by_category.get(category, 0),
            annual_saving_total=sum(
                s.annual_saving for s in report.by_kind if s.category is category
            ),
            by_category=report.by_category,
            by_kind=[
                KindSummaryRead.model_validate(s, from_attributes=True)
                for s in report.by_kind
                if s.category is category
            ],
            insights=[InsightRead.model_validate(i, from_attributes=True) for i in filtered],
        )

    return InsightReportRead.model_validate(report, from_attributes=True)
