"""保守期限 / EoL の残日数とアラート重要度を判定するロジック。

しきい値は設定で変更できる（既定: 90日以内=critical, 365日以内=warning）。
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from datetime import date

from app.config import settings
from app.models import Device
from app.services import appsettings


class Severity(enum.StrEnum):
    EXPIRED = "expired"  # すでに期限切れ
    CRITICAL = "critical"  # 期限が近い（既定90日以内）
    WARNING = "warning"  # 要計画（既定365日以内）
    OK = "ok"
    UNKNOWN = "unknown"  # 日付が未登録

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.EXPIRED: 4,
    Severity.CRITICAL: 3,
    Severity.WARNING: 2,
    Severity.UNKNOWN: 1,
    Severity.OK: 0,
}


def days_until(target: date | None, today: date | None = None) -> int | None:
    """今日から対象日までの日数。過去なら負値、未登録なら None。"""
    if target is None:
        return None
    return (target - (today or date.today())).days


def severity_for(target: date | None, today: date | None = None) -> Severity:
    remaining = days_until(target, today)
    if remaining is None:
        return Severity.UNKNOWN
    if remaining < 0:
        return Severity.EXPIRED
    if remaining <= appsettings.number("critical_days", settings.critical_days):
        return Severity.CRITICAL
    if remaining <= appsettings.number("warning_days", settings.warning_days):
        return Severity.WARNING
    return Severity.OK


def worst(*severities: Severity) -> Severity:
    """最も重い重要度を返す。UNKNOWN は OK より重い扱い（データ未整備を見逃さないため）。"""
    return max(severities, key=lambda s: s.rank)


@dataclass(frozen=True)
class LifecycleStatus:
    """1台の機器のライフサイクル評価結果。API のレスポンスにそのまま載る。"""

    # 実際に判定へ使った日付と、その出どころ（機器個別の入力か、型番の製品マスターか）
    effective_eos_date: date | None
    effective_eol_date: date | None
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


def evaluate(device: Device, today: date | None = None) -> LifecycleStatus:
    today = today or date.today()

    # 有効な保守契約のうち最も遠い終了日を「保守切れ日」とみなす
    ends = [c.end_date for c in device.contracts if c.end_date is not None]
    maintenance_end = max(ends) if ends else None

    # 機器個別の入力を優先し、無ければ型番の製品マスターを使う。
    # 個体の事情（特別な延長保守など）を上書きできる余地を残すため。
    product = getattr(device, "product", None)
    eos_date = device.eos_date or (product.eos_date if product else None)
    eol_date = device.eol_date or (product.eol_date if product else None)

    if device.eol_date is not None:
        eol_source = "device"
    elif product is not None and product.eol_date is not None:
        eol_source = "product"
    else:
        eol_source = None

    maintenance_sev = severity_for(maintenance_end, today)
    eos_sev = severity_for(eos_date, today)
    eol_sev = severity_for(eol_date, today)

    age_years = None
    if device.install_date is not None:
        age_years = round((today - device.install_date).days / 365.25, 1)

    # 撤去済みの機器はアラート対象から外す
    overall = Severity.OK if device.status.value == "retired" else worst(maintenance_sev, eol_sev)

    return LifecycleStatus(
        effective_eos_date=eos_date,
        effective_eol_date=eol_date,
        eol_source=eol_source,
        maintenance_end=maintenance_end,
        days_to_maintenance_end=days_until(maintenance_end, today),
        maintenance_severity=maintenance_sev,
        days_to_eos=days_until(eos_date, today),
        eos_severity=eos_sev,
        days_to_eol=days_until(eol_date, today),
        eol_severity=eol_sev,
        overall_severity=overall,
        age_years=age_years,
    )
