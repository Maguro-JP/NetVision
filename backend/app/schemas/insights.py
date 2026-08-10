from pydantic import BaseModel

from app.services.insights import InsightCategory, InsightKind
from app.services.lifecycle import Severity


class InsightRead(BaseModel):
    kind: InsightKind
    category: InsightCategory
    title: str
    severity: Severity
    detail: str
    device_id: int | None
    device_hostname: str | None
    annual_saving: float | None
    recommendation: str


class KindSummaryRead(BaseModel):
    kind: InsightKind
    category: InsightCategory
    title: str
    severity: Severity
    count: int
    annual_saving: float


class InsightReportRead(BaseModel):
    total: int
    annual_saving_total: float
    by_category: dict[str, int]
    #: 種類ごとの件数。件数が多いときはまずこちらを見る
    by_kind: list[KindSummaryRead] = []
    insights: list[InsightRead]
