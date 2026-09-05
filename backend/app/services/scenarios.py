"""将来構成シナリオの評価。

「今の構成」と「案を適用した構成」を同じ指標で並べ、差分を出す。
台数・年間保守費・期限切れ台数・平均経過年数と、必要な投資額を見る。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import Device, DeviceStatus
from app.models.scenario import Scenario, ScenarioAction, ScenarioItem
from app.services.device_query import DeviceFilters, query_alerts, query_devices
from app.services.lifecycle import Severity, evaluate


@dataclass
class ConfigurationMetrics:
    """ある時点の構成を評価した指標。"""

    device_count: int = 0
    annual_maintenance_cost: float = 0.0
    expired_count: int = 0  # サポート切れ / 保守切れ
    at_risk_count: int = 0  # 1年以内に期限を迎える
    unsupported_count: int = 0  # EoL 超過
    average_age_years: float | None = None


@dataclass
class ScenarioSummary:
    scenario_id: int
    name: str
    status: str
    evaluated_on: date
    current: ConfigurationMetrics
    projected: ConfigurationMetrics
    investment: float  # 初期費用の合計
    annual_cost_delta: float  # 年間保守費の増減（マイナスなら削減）
    item_counts: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def _active_cost(device: Device, on: date) -> float:
    return sum(
        float(c.annual_cost or 0) for c in device.contracts if c.end_date and c.end_date >= on
    )


def _measure_existing(devices: list[Device], on: date) -> ConfigurationMetrics:
    metrics = ConfigurationMetrics()
    ages: list[float] = []

    for device in devices:
        status = evaluate(device, on)
        metrics.device_count += 1
        metrics.annual_maintenance_cost += _active_cost(device, on)

        if status.overall_severity is Severity.EXPIRED:
            metrics.expired_count += 1
        if status.overall_severity in (Severity.EXPIRED, Severity.CRITICAL, Severity.WARNING):
            metrics.at_risk_count += 1
        if status.eol_severity is Severity.EXPIRED:
            metrics.unsupported_count += 1
        if status.age_years is not None:
            ages.append(status.age_years)

    metrics.average_age_years = round(sum(ages) / len(ages), 1) if ages else None
    return metrics


@dataclass
class _PlannedDevice:
    """シナリオで新しく入る機器。既存機器と同じ指標で数えるための最小の器。"""

    quantity: int
    annual_cost: float
    install_date: date | None
    eol_date: date | None


def _measure_projected(
    kept: list[Device], planned: list[_PlannedDevice], on: date
) -> ConfigurationMetrics:
    metrics = _measure_existing(kept, on)
    ages: list[float] = [
        round((on - d.install_date).days / 365.25, 1) for d in kept if d.install_date is not None
    ]

    for item in planned:
        metrics.device_count += item.quantity
        metrics.annual_maintenance_cost += item.annual_cost * item.quantity

        if item.eol_date is not None:
            remaining = (item.eol_date - on).days
            if remaining < 0:
                metrics.expired_count += item.quantity
                metrics.unsupported_count += item.quantity
                metrics.at_risk_count += item.quantity
            elif remaining <= 365:
                metrics.at_risk_count += item.quantity

        # 導入予定日が未来なら経過年数は 0 とみなす
        if item.install_date is not None:
            age = max(0.0, round((on - item.install_date).days / 365.25, 1))
            ages.extend([age] * item.quantity)
        else:
            ages.extend([0.0] * item.quantity)

    metrics.average_age_years = round(sum(ages) / len(ages), 1) if ages else None
    return metrics


def summarize(db: Session, scenario: Scenario) -> ScenarioSummary:
    on = scenario.target_date or date.today()

    # 撤去済みは現状構成に含めない（今動いているものとの比較にしたいため）
    devices = [
        d for d in query_devices(db, DeviceFilters()) if d.status is not DeviceStatus.RETIRED
    ]
    by_id = {d.id: d for d in devices}

    current = _measure_existing(devices, on)

    removed_ids: set[int] = set()
    planned: list[_PlannedDevice] = []
    counts: dict[str, int] = {}
    warnings: list[str] = []
    investment = 0.0

    for item in scenario.items:
        counts[item.action] = counts.get(item.action, 0) + 1

        if item.action in (ScenarioAction.REPLACE, ScenarioAction.REMOVE):
            if item.device_id is None or item.device_id not in by_id:
                warnings.append(
                    f"「{item.device_label or '(対象不明)'}」は台帳に見つからないため、"
                    "現状構成からの除外を反映していません"
                )
            else:
                removed_ids.add(item.device_id)

        if item.action in (ScenarioAction.REPLACE, ScenarioAction.ADD):
            if item.planned_model is None:
                label = item.device_label or "(項目)"
                warnings.append(f"「{label}」に導入機器の型番が未入力です")
            investment += float(item.unit_cost or 0) * item.quantity
            planned.append(
                _PlannedDevice(
                    quantity=item.quantity,
                    annual_cost=float(item.planned_annual_cost or 0),
                    install_date=item.planned_install_date,
                    eol_date=item.planned_eol_date,
                )
            )

    # 費用を入れていない項目があると、合計は「0円」ではなく「まだ分からない」。
    # そこを混ぜると、投資額を見た人が入れ替えを無料だと受け取る
    priced = sum(
        1
        for item in scenario.items
        if item.action in (ScenarioAction.REPLACE, ScenarioAction.ADD)
        and item.unit_cost is not None
    )
    to_buy = counts.get(ScenarioAction.REPLACE, 0) + counts.get(ScenarioAction.ADD, 0)
    if to_buy and priced < to_buy:
        warnings.append(
            f"導入費用が入っているのは {to_buy} 件中 {priced} 件です。"
            "投資額はその分だけの合計で、全体の見積もりではありません"
        )

    kept = [d for d in devices if d.id not in removed_ids]
    projected = _measure_projected(kept, planned, on)

    return ScenarioSummary(
        scenario_id=scenario.id,
        name=scenario.name,
        status=str(scenario.status),
        evaluated_on=on,
        current=current,
        projected=projected,
        investment=investment,
        annual_cost_delta=projected.annual_maintenance_cost - current.annual_maintenance_cost,
        item_counts=counts,
        warnings=warnings,
    )


def generate_items(db: Session, scenario: Scenario, within_days: int = 730) -> list[ScenarioItem]:
    """期限が近い機器から、入れ替え案のたたき台を作る。

    ゼロから項目を並べるのは大変なので、まず「保守切れ / EoL が近い機器」を
    replace として並べ、そこから人が編集する前提にしている。
    """
    on = scenario.target_date or date.today()
    existing_targets = {item.device_id for item in scenario.items if item.device_id}

    created: list[ScenarioItem] = []
    for device in query_alerts(db, within_days, today=on):
        if device.id in existing_targets:
            continue
        status = evaluate(device, on)
        soonest = min(
            d for d in (status.days_to_maintenance_end, status.days_to_eol) if d is not None
        )

        created.append(
            ScenarioItem(
                scenario_id=scenario.id,
                action=ScenarioAction.REPLACE,
                device_id=device.id,
                device_label=device.hostname,
                planned_vendor=device.vendor,
                # 後継機は人が決める。まずは現行と同じ型番を仮置きする
                planned_model=device.model,
                quantity=1,
                planned_annual_cost=_active_cost(device, on) or None,
                notes=(
                    f"保守期限/EoL まで残り {soonest} 日のため自動抽出"
                    if soonest >= 0
                    else f"保守期限/EoL を {abs(soonest)} 日超過しているため自動抽出"
                ),
            )
        )

    db.add_all(created)
    db.flush()
    return created
