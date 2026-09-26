from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.reference import ReferenceSource
from app.schemas.types import refuse_null


class ProductReferenceBase(BaseModel):
    vendor: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=120)
    product_name: str | None = None
    eos_date: date | None = None
    eol_date: date | None = None
    end_of_sw_date: date | None = None
    last_support_date: date | None = None
    recommended_replacement: str | None = None
    source: ReferenceSource = ReferenceSource.MANUAL
    source_url: str | None = None
    fetched_at: date | None = None
    notes: str | None = None


class ProductReferenceCreate(ProductReferenceBase):
    pass


class ProductReferenceUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=120)
    product_name: str | None = None
    eos_date: date | None = None
    eol_date: date | None = None
    end_of_sw_date: date | None = None
    last_support_date: date | None = None
    recommended_replacement: str | None = None
    source: ReferenceSource | None = None
    source_url: str | None = None
    fetched_at: date | None = None
    notes: str | None = None

    _not_null = field_validator("vendor", "model", "source", mode="before")(refuse_null)


class ProductReferenceRead(ProductReferenceBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_count: int = 0
    # 情報取得日からの経過日数。閉域運用ではここが鮮度の唯一の手がかりになる。
    age_days: int | None = None
    stale: bool = False


class ProductImportRowRead(BaseModel):
    row_number: int
    vendor: str | None
    model: str | None
    action: str
    message: str | None = None


class ProductImportResultRead(BaseModel):
    total_rows: int
    created: int
    updated: int
    unchanged: int
    errors: int
    mapping: dict[str, str]
    unmapped_columns: list[str]
    missing_required_fields: list[str]
    rows: list[ProductImportRowRead]


class VulnerabilityAffectRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    vendor: str
    model: str | None
    os_name: str | None
    version_start_including: str | None
    version_start_excluding: str | None
    version_end_including: str | None
    version_end_excluding: str | None
    exact_version: str | None
    fixed_version: str | None


class VulnerabilityRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    cve_id: str
    title: str | None
    description: str | None
    cvss_score: float | None
    cvss_severity: str | None
    published: date | None
    last_modified: date | None
    known_exploited: bool
    remediation_due: date | None
    source: ReferenceSource
    source_url: str | None
    fetched_at: date | None
    affected_device_count: int = 0


class VulnerabilityDetail(VulnerabilityRead):
    affects: list[VulnerabilityAffectRead] = []


class VulnerabilityImportResultRead(BaseModel):
    format: str
    total: int
    created: int
    updated: int
    skipped: int
    affects_created: int
    messages: list[str] = []


class DeviceVulnerabilityRead(BaseModel):
    device_id: int
    hostname: str
    cve_id: str
    title: str | None
    cvss_score: float | None
    cvss_severity: str | None
    known_exploited: bool
    fixed_version: str | None
    matched_on: str
    source: str
    fetched_at: date | None
    #: 版数が読めていないため、当たるかどうかを言えない
    undetermined: bool = False


class VulnerabilitySummary(BaseModel):
    """脆弱性の突合結果のサマリ。"""

    total_vulnerabilities: int
    devices_affected: int
    known_exploited_findings: int
    critical_findings: int
    high_findings: int
    devices_without_version: int
    #: 型番は当たるのに版数が無く、判定できなかった件数
    undetermined_findings: int = 0
    findings: list[DeviceVulnerabilityRead]


class ReferenceFreshness(BaseModel):
    """リファレンスの鮮度。古い情報で判断していないかを見るための指標。"""

    product_count: int
    products_with_dates: int
    products_stale: int
    products_never_fetched: int
    oldest_product_fetch: date | None
    vulnerability_count: int
    vulnerabilities_stale: int
    oldest_vulnerability_fetch: date | None
    stale_after_days: int
    devices_total: int
    devices_with_product_reference: int
    devices_without_reference: list[str]
