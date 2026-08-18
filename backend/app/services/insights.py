"""登録済みの台帳から「無駄」と「リスク」を洗い出す。

外部から追加のデータを取らず、いま入っている情報だけで判定できるものに絞っている。
それぞれの指摘には、可能なら年額いくら削減できるかを添える。
更改の稟議で使えるのは、結局そこだからである。
"""

from __future__ import annotations

import enum
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from heapq import heappush, heappushpop
from typing import TYPE_CHECKING

from app.models import Device, DeviceStatus, MaintenanceContract
from app.services.lifecycle import Severity, evaluate

if TYPE_CHECKING:
    from app.models.reference import Vulnerability


class InsightKind(enum.StrEnum):
    # 無駄（費用削減の余地）
    CONTRACT_ON_RETIRED = "contract_on_retired"
    CONTRACT_ON_STANDBY = "contract_on_standby"
    DUPLICATE_CONTRACT = "duplicate_contract"
    CONTRACT_AFTER_EOL = "contract_after_eol"
    # リスク
    ACTIVE_AFTER_EOL = "active_after_eol"
    ACTIVE_WITHOUT_CONTRACT = "active_without_contract"
    AGING_DEVICE = "aging_device"
    KNOWN_EXPLOITED_VULNERABILITY = "known_exploited_vulnerability"
    CRITICAL_VULNERABILITY = "critical_vulnerability"
    # 台帳の整備
    MISSING_LIFECYCLE_DATA = "missing_lifecycle_data"
    MISSING_SITE = "missing_site"
    MISSING_PRODUCT_REFERENCE = "missing_product_reference"
    MISSING_OS_VERSION = "missing_os_version"
    # 集約の余地
    MIXED_PROVIDERS = "mixed_providers"


class InsightCategory(enum.StrEnum):
    COST = "cost"  # 無駄をなくす
    RISK = "risk"  # リスクを減らす
    DATA = "data"  # 台帳を整える
    CONSOLIDATION = "consolidation"  # まとめる


KIND_META: dict[InsightKind, tuple[InsightCategory, str, Severity]] = {
    InsightKind.CONTRACT_ON_RETIRED: (
        InsightCategory.COST,
        "撤去済みの機器に保守契約が残っています",
        Severity.EXPIRED,
    ),
    InsightKind.CONTRACT_ON_STANDBY: (
        InsightCategory.COST,
        "予備機に稼働機と同等の保守が付いています",
        Severity.WARNING,
    ),
    InsightKind.DUPLICATE_CONTRACT: (
        InsightCategory.COST,
        "同じ期間の保守契約が重複しています",
        Severity.CRITICAL,
    ),
    InsightKind.CONTRACT_AFTER_EOL: (
        InsightCategory.COST,
        "サポート終了後まで保守契約が伸びています",
        Severity.WARNING,
    ),
    InsightKind.ACTIVE_AFTER_EOL: (
        InsightCategory.RISK,
        "サポート終了済みの機器が稼働しています",
        Severity.EXPIRED,
    ),
    InsightKind.ACTIVE_WITHOUT_CONTRACT: (
        InsightCategory.RISK,
        "稼働中なのに有効な保守契約がありません",
        Severity.CRITICAL,
    ),
    InsightKind.AGING_DEVICE: (
        InsightCategory.RISK,
        "導入から年数が経過しており更改の検討時期です",
        Severity.WARNING,
    ),
    InsightKind.KNOWN_EXPLOITED_VULNERABILITY: (
        InsightCategory.RISK,
        "悪用が確認されている脆弱性の影響を受けています",
        Severity.EXPIRED,
    ),
    InsightKind.CRITICAL_VULNERABILITY: (
        InsightCategory.RISK,
        "深刻度の高い脆弱性の影響を受けています",
        Severity.CRITICAL,
    ),
    InsightKind.MISSING_PRODUCT_REFERENCE: (
        InsightCategory.DATA,
        "型番の製品リファレンスが未登録です",
        Severity.UNKNOWN,
    ),
    InsightKind.MISSING_OS_VERSION: (
        InsightCategory.DATA,
        "OSバージョンが未登録で、脆弱性の判定ができません",
        Severity.UNKNOWN,
    ),
    InsightKind.MISSING_LIFECYCLE_DATA: (
        InsightCategory.DATA,
        "EoL も保守期限も未登録で、期限を判定できません",
        Severity.UNKNOWN,
    ),
    InsightKind.MISSING_SITE: (
        InsightCategory.DATA,
        "設置拠点が未登録です",
        Severity.UNKNOWN,
    ),
    InsightKind.MIXED_PROVIDERS: (
        InsightCategory.CONSOLIDATION,
        "同じ型番の機器で保守ベンダーが分かれています",
        Severity.OK,
    ),
}

# 更改検討の目安（年）。ネットワーク機器の一般的な更改周期を既定値にしている。
AGING_YEARS = 7.0


@dataclass
class Insight:
    kind: InsightKind
    category: InsightCategory
    title: str
    severity: Severity
    detail: str
    device_id: int | None = None
    device_hostname: str | None = None
    # 年額いくら削減できるか（削減余地がある指摘のみ）
    annual_saving: float | None = None
    recommendation: str = ""


@dataclass
class KindSummary:
    """指摘の種類ごとのまとめ。"""

    kind: InsightKind
    category: InsightCategory
    title: str
    severity: Severity
    count: int
    annual_saving: float


@dataclass
class InsightReport:
    total: int
    annual_saving_total: float
    by_category: dict[str, int] = field(default_factory=dict)
    #: 種類ごとの件数。16万台では同じ文言が16万行並ぶので、まずこちらを見る
    by_kind: list[KindSummary] = field(default_factory=list)
    insights: list[Insight] = field(default_factory=list)


def _active_contracts(device: Device, today: date) -> list[MaintenanceContract]:
    return [c for c in device.contracts if c.end_date and c.end_date >= today]


def _cost(contract: MaintenanceContract) -> float:
    return float(contract.annual_cost) if contract.annual_cost is not None else 0.0


def _make(kind: InsightKind, device: Device | None, detail: str, **kwargs) -> Insight:
    category, title, severity = KIND_META[kind]
    return Insight(
        kind=kind,
        category=category,
        title=title,
        severity=severity,
        detail=detail,
        device_id=device.id if device else None,
        device_hostname=device.hostname if device else None,
        **kwargs,
    )


class _Collector:
    """指摘の件数は全件から数え、返す中身だけ重い順に keep 件残す。"""

    def __init__(self, keep: int | None) -> None:
        self.keep = keep
        self.total = 0
        self.annual_saving_total = 0.0
        self.by_category: dict[str, int] = {}
        self.summaries: dict[InsightKind, KindSummary] = {}
        self._all: list[Insight] = []
        self._heap: list[tuple] = []
        self._seq = 0

    def emit(self, kind: InsightKind, device: Device | None, detail: str, **kwargs) -> None:
        category, title, severity = KIND_META[kind]
        saving = kwargs.get("annual_saving") or 0
        self._count(kind, category, title, severity, saving)
        if self.keep is not None and not self._would_keep(severity.rank, saving):
            return
        self._retain(_make(kind, device, detail, **kwargs), severity.rank, saving)

    def absorb(self, insight: Insight) -> None:
        saving = insight.annual_saving or 0
        self._count(insight.kind, insight.category, insight.title, insight.severity, saving)
        if self.keep is not None and not self._would_keep(insight.severity.rank, saving):
            return
        self._retain(insight, insight.severity.rank, saving)

    def _count(
        self,
        kind: InsightKind,
        category: InsightCategory,
        title: str,
        severity: Severity,
        saving: float,
    ) -> None:
        self.total += 1
        self.annual_saving_total += saving
        self.by_category[category] = self.by_category.get(category, 0) + 1
        summary = self.summaries.get(kind)
        if summary is None:
            self.summaries[kind] = KindSummary(
                kind=kind,
                category=category,
                title=title,
                severity=severity,
                count=1,
                annual_saving=saving,
            )
        else:
            summary.count += 1
            summary.annual_saving += saving

    def _would_keep(self, rank: int, saving: float) -> bool:
        if len(self._heap) < (self.keep or 0):
            return True
        worst_rank, worst_saving = self._heap[0][0], self._heap[0][1]
        return (rank, saving) > (worst_rank, worst_saving)

    def _retain(self, insight: Insight, rank: int, saving: float) -> None:
        if self.keep is None:
            self._all.append(insight)
            return
        self._seq += 1
        item = (rank, saving, -self._seq, insight)
        if len(self._heap) < self.keep:
            heappush(self._heap, item)
        else:
            heappushpop(self._heap, item)

    def report(self) -> InsightReport:
        insights = self._all if self.keep is None else [item[3] for item in self._heap]
        insights.sort(key=lambda i: (-i.severity.rank, -(i.annual_saving or 0)))
        by_kind = sorted(
            self.summaries.values(), key=lambda s: (-s.severity.rank, -s.annual_saving, -s.count)
        )
        return InsightReport(
            total=self.total,
            annual_saving_total=self.annual_saving_total,
            by_category=self.by_category,
            by_kind=by_kind,
            insights=insights,
        )


def analyze(
    devices: Iterable[Device],
    today: date | None = None,
    vulnerabilities: list[Vulnerability] | None = None,
    keep: int | None = None,
) -> InsightReport:
    """台帳から読み取れる無駄とリスクを洗い出す。

    `keep` を渡すと、返す指摘を重い順にその件数までに絞る。件数や削減見込みは
    絞る前の全件から出すので、数字は変わらない。

    16万台の環境では指摘が40万件近くになる。全部返すと 150MB の応答になり、
    受け取った画面側が固まる。そもそも同じ文言が16万行並んでも読めないので、
    まず種類ごとの件数（by_kind）を見て、中身は必要な分だけ見る形にする。
    """
    today = today or date.today()
    collector = _Collector(keep)
    mixed: dict[str, dict[str, set[str]]] = {}

    for device in devices:
        status = evaluate(device, today)
        active = _active_contracts(device, today)
        active_cost = sum(_cost(c) for c in active)

        # --- 無駄 -----------------------------------------------------------
        if device.status is DeviceStatus.RETIRED and active:
            collector.emit(
                InsightKind.CONTRACT_ON_RETIRED,
                device,
                f"撤去済みですが {len(active)} 件の保守契約が有効なままです"
                f"（最終終了日 {max(c.end_date for c in active):%Y/%m/%d}）",
                annual_saving=active_cost or None,
                recommendation="保守契約の解約を検討してください。",
            )

        if device.status is DeviceStatus.STANDBY and active_cost > 0:
            collector.emit(
                InsightKind.CONTRACT_ON_STANDBY,
                device,
                f"予備機に年額 {active_cost:,.0f} 円の保守が付いています",
                # 予備機はサービスレベルを下げる想定で、半額を削減余地とみなす
                annual_saving=active_cost / 2,
                recommendation="予備機向けにサービスレベルを下げられないか確認してください。",
            )

        # 同じ期間に重なる契約は二重払いの可能性が高い
        for index, first in enumerate(active):
            for second in active[index + 1 :]:
                if first.start_date <= second.end_date and second.start_date <= first.end_date:
                    collector.emit(
                        InsightKind.DUPLICATE_CONTRACT,
                        device,
                        f"「{first.provider}」と「{second.provider}」の契約期間が重なっています",
                        annual_saving=min(_cost(first), _cost(second)) or None,
                        recommendation="二重契約になっていないか確認してください。",
                    )

        if status.effective_eol_date and active:
            latest = max(c.end_date for c in active)
            if latest > status.effective_eol_date:
                over_days = (latest - status.effective_eol_date).days
                collector.emit(
                    InsightKind.CONTRACT_AFTER_EOL,
                    device,
                    f"サポート終了({status.effective_eol_date:%Y/%m/%d})より "
                    f"{over_days} 日先まで保守契約があります",
                    recommendation=(
                        "EoL 以降はベンダー保守が受けられない場合があります。"
                        "契約期間を見直してください。"
                    ),
                )

        # --- リスク ---------------------------------------------------------
        if device.status is DeviceStatus.ACTIVE:
            if status.eol_severity is Severity.EXPIRED:
                collector.emit(
                    InsightKind.ACTIVE_AFTER_EOL,
                    device,
                    f"サポート終了から {abs(status.days_to_eol or 0)} 日経過しています",
                    recommendation="脆弱性の修正が提供されません。更改を優先してください。",
                )

            if not active:
                collector.emit(
                    InsightKind.ACTIVE_WITHOUT_CONTRACT,
                    device,
                    "有効な保守契約がありません"
                    if device.contracts
                    else "保守契約が登録されていません",
                    recommendation="障害時の復旧手段を確認し、必要なら契約してください。",
                )

            if status.age_years is not None and status.age_years >= AGING_YEARS:
                collector.emit(
                    InsightKind.AGING_DEVICE,
                    device,
                    f"導入から {status.age_years} 年経過しています",
                    recommendation="更改計画に含めることを検討してください。",
                )

            for finding in _vulnerability_insights(device, vulnerabilities or []):
                collector.absorb(finding)

        # --- 台帳の整備 -----------------------------------------------------
        if device.status is not DeviceStatus.RETIRED:
            for contract in active:
                mixed.setdefault(device.model, {}).setdefault(contract.provider, set()).add(
                    device.hostname
                )
            if getattr(device, "product", None) is None:
                collector.emit(
                    InsightKind.MISSING_PRODUCT_REFERENCE,
                    device,
                    f"{device.vendor} {device.model} の EoL 情報が製品マスターにありません",
                    recommendation=(
                        "リファレンス管理から型番を登録すると、同じ型番の機器すべてに反映されます。"
                    ),
                )
            if not device.os_version:
                collector.emit(
                    InsightKind.MISSING_OS_VERSION,
                    device,
                    "OSバージョンが分からないため、該当する脆弱性を判定できません",
                    recommendation="Config を取り込むと自動で読み取れます。",
                )
            if status.effective_eol_date is None and status.maintenance_end is None:
                collector.emit(
                    InsightKind.MISSING_LIFECYCLE_DATA,
                    device,
                    "EoL・保守期限のどちらも未登録です",
                    recommendation="ベンダーのEoX情報と保守契約を確認して登録してください。",
                )
            if device.site_id is None:
                collector.emit(
                    InsightKind.MISSING_SITE,
                    device,
                    "設置拠点が未登録です",
                    recommendation="拠点を登録すると、拠点単位の更改計画が立てられます。",
                )

    for insight in _mixed_provider_insights(mixed):
        collector.absorb(insight)

    return collector.report()


def analyze_from_db(
    db,
    *,
    site_id: int | None = None,
    today: date | None = None,
    vulnerabilities: list[Vulnerability] | None = None,
    keep: int | None = 200,
) -> InsightReport:
    """台帳全体を ORM 化せずに指摘を出す。ノートPCの導入テスト向け。

    件数は SQL、文言は種類ごとに必要な機器だけ読む。
    """
    from app.services import insight_query as iq

    today = today or date.today()
    sample = keep or 200
    stats = iq.collect_kind_stats(db, site_id=site_id, today=today, sample=sample)
    mixed = iq.mixed_provider_insights(db, site_id=site_id, today=today)

    sample_ids: list[int] = []
    seen: set[int] = set()
    for stat in stats:
        for device_id in stat.sample_ids:
            if device_id not in seen:
                seen.add(device_id)
                sample_ids.append(device_id)

    devices = iq.load_devices(db, sample_ids)
    details = analyze(devices, today=today, keep=None, vulnerabilities=None)
    insights = [i for i in details.insights if i.kind is not InsightKind.MIXED_PROVIDERS]
    insights.extend(mixed)

    vuln_summaries: list[KindSummary] = []
    if vulnerabilities:
        vuln_report = _vulnerability_report(
            db, site_id=site_id, vulnerabilities=vulnerabilities, keep=keep
        )
        insights.extend(vuln_report.insights)
        vuln_summaries = vuln_report.by_kind
        vuln_total = vuln_report.total
        vuln_saving = vuln_report.annual_saving_total
    else:
        vuln_total = 0
        vuln_saving = 0.0

    insights.sort(key=lambda i: (-i.severity.rank, -(i.annual_saving or 0)))
    if keep is not None:
        insights = insights[:keep]

    by_kind = iq.summaries_from_stats(stats, extra=vuln_summaries)
    if mixed:
        category, title, severity = KIND_META[InsightKind.MIXED_PROVIDERS]
        by_kind.append(
            KindSummary(
                kind=InsightKind.MIXED_PROVIDERS,
                category=category,
                title=title,
                severity=severity,
                count=len(mixed),
                annual_saving=0,
            )
        )
        by_kind.sort(key=lambda s: (-s.severity.rank, -s.annual_saving, -s.count))

    by_category: dict[str, int] = {}
    for summary in by_kind:
        by_category[summary.category] = by_category.get(summary.category, 0) + summary.count

    return InsightReport(
        total=sum(s.count for s in stats) + len(mixed) + vuln_total,
        annual_saving_total=sum(s.annual_saving for s in stats) + vuln_saving,
        by_category=by_category,
        by_kind=by_kind,
        insights=insights,
    )


def _vulnerability_report(
    db,
    *,
    site_id: int | None,
    vulnerabilities: list,
    keep: int | None,
) -> InsightReport:
    """OS が入っている稼働機だけを見て、脆弱性の指摘を数える。"""
    from sqlalchemy import select

    from app.models import Device, DeviceStatus
    from app.services.insight_query import load_devices

    collector = _Collector(keep)
    last_id = 0
    chunk = 200
    while True:
        stmt = (
            select(Device.id)
            .where(
                Device.status == DeviceStatus.ACTIVE,
                Device.os_version.is_not(None),
                Device.os_version != "",
                Device.id > last_id,
            )
            .order_by(Device.id)
            .limit(chunk)
        )
        if site_id is not None:
            stmt = stmt.where(Device.site_id == site_id)
        ids = list(db.scalars(stmt))
        if not ids:
            break
        last_id = ids[-1]
        for device in load_devices(db, ids):
            for finding in _vulnerability_insights(device, vulnerabilities):
                collector.absorb(finding)
    return collector.report()


def _vulnerability_insights(device: Device, vulnerabilities: list) -> list[Insight]:
    """該当する脆弱性を指摘に変える。悪用確認済みを最優先で挙げる。"""
    from app.services.reference import match_device

    findings = match_device(device, vulnerabilities)
    if not findings:
        return []

    exploited = [f for f in findings if f.known_exploited]
    critical = [f for f in findings if not f.known_exploited and (f.cvss_score or 0) >= 9.0]

    insights: list[Insight] = []
    if exploited:
        ids = ", ".join(f.cve_id for f in exploited[:5])
        fixed = next((f.fixed_version for f in exploited if f.fixed_version), None)
        insights.append(
            _make(
                InsightKind.KNOWN_EXPLOITED_VULNERABILITY,
                device,
                f"{len(exploited)}件（{ids}）。稼働中のバージョン {device.os_version}",
                recommendation=(
                    f"{fixed} 以降へのアップグレードを最優先で検討してください。"
                    if fixed
                    else "ベンダーの案内に従い、最優先で対処してください。"
                ),
            )
        )
    if critical:
        ids = ", ".join(f.cve_id for f in critical[:5])
        insights.append(
            _make(
                InsightKind.CRITICAL_VULNERABILITY,
                device,
                f"CVSS 9.0 以上が {len(critical)}件（{ids}）",
                recommendation="アップグレード計画に含めてください。",
            )
        )
    return insights


def _mixed_provider_insights(by_model: dict[str, dict[str, set[str]]]) -> list[Insight]:
    """同じ型番なのに保守ベンダーが分かれているものを集約候補として挙げる。"""
    insights: list[Insight] = []
    for model, providers in by_model.items():
        if len(providers) < 2:
            continue
        detail = " / ".join(f"{name}: {len(hosts)}台" for name, hosts in sorted(providers.items()))
        insights.append(
            Insight(
                kind=InsightKind.MIXED_PROVIDERS,
                category=InsightCategory.CONSOLIDATION,
                title=KIND_META[InsightKind.MIXED_PROVIDERS][1],
                severity=Severity.OK,
                detail=f"{model}: {detail}",
                recommendation="契約をまとめると単価と管理の両方で有利になる可能性があります。",
            )
        )
    return insights
