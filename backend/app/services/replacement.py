"""更改支援：年度別の更改計画と、更改時の影響範囲。

稟議で必要になるのは「いつ・何台・いくら」なので、年度で束ねて出す。
年度は日本の会計年度（4月〜翌3月）で切る。

影響範囲は「その機器を止めたとき、通信経路が失われる機器」。
トポロジ上でその機器を取り除き、基幹から辿れなくなるノードを数える。
作業の段取り（何時に止めるか、誰に周知するか）を決めるのに要る情報なので、
台数ではなく機器名で出す。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy.orm import Session

from app.models import Device, DeviceStatus, Link
from app.services.device_query import DeviceFilters, query_devices
from app.services.lifecycle import Severity, evaluate

# 会計年度の開始月（日本の年度）
FISCAL_START_MONTH = 4


def fiscal_year_of(day: date) -> int:
    """その日が属する年度。2026-03-31 は 2025年度。"""
    return day.year if day.month >= FISCAL_START_MONTH else day.year - 1


def fiscal_year_label(year: int) -> str:
    return f"{year}年度"


@dataclass
class ReplacementTarget:
    device_id: int
    hostname: str
    vendor: str | None
    model: str | None
    site_name: str | None
    # 更改が必要になる理由と、その期日
    reason: str
    due_on: date | None
    severity: str
    estimated_cost: float | None
    impact_count: int = 0  # この機器を止めると経路が失われる機器の数


@dataclass
class FiscalYearPlan:
    fiscal_year: int
    label: str
    device_count: int
    estimated_cost: float
    targets: list[ReplacementTarget] = field(default_factory=list)


@dataclass
class ReplacementPlan:
    evaluated_on: date
    years: list[FiscalYearPlan] = field(default_factory=list)
    # 期日が分からず年度に割り付けられなかったもの（台帳の抜け）
    unscheduled: list[ReplacementTarget] = field(default_factory=list)
    total_cost: float = 0.0
    total_devices: int = 0


def _due_and_reason(device: Device, today: date) -> tuple[date | None, str, str]:
    """更改の期日として何を見るか。

    保守期限と EoL のうち**早い方**が実質の期限になる。
    保守が切れれば壊れても直せず、EoL を過ぎればそもそも部品が無い。
    """
    status = evaluate(device, today)
    candidates: list[tuple[date, str]] = []

    if status.maintenance_end is not None:
        candidates.append((status.maintenance_end, "保守期限"))
    if status.effective_eol_date is not None:
        candidates.append((status.effective_eol_date, "サポート終了(EoL)"))

    if not candidates:
        return None, "期限が未登録", str(status.overall_severity)

    due, reason = min(candidates, key=lambda pair: pair[0])
    return due, reason, str(status.overall_severity)


def _estimated_cost(device: Device) -> float | None:
    """更改費用の見込み。

    見積が無い段階では、今払っている年間保守費から機器価格を粗く見る。
    根拠が弱い数字なので、画面では「概算」と明示する。
    """
    annual = sum(float(c.annual_cost or 0) for c in device.contracts)
    if annual <= 0:
        return None
    # 保守は機器価格の 10%/年 前後が相場。5年分の保守 ≒ 機器価格の 1/2 として逆算
    return round(annual * 10, -3)


def build_plan(db: Session, *, years: int = 5, today: date | None = None) -> ReplacementPlan:
    today = today or date.today()
    start_year = fiscal_year_of(today)

    devices = [
        d for d in query_devices(db, DeviceFilters()) if d.status is not DeviceStatus.RETIRED
    ]
    impacts = impact_counts(db)

    buckets: dict[int, FiscalYearPlan] = {
        year: FiscalYearPlan(year, fiscal_year_label(year), 0, 0.0)
        for year in range(start_year, start_year + years)
    }
    plan = ReplacementPlan(evaluated_on=today)

    for device in devices:
        due, reason, severity = _due_and_reason(device, today)
        cost = _estimated_cost(device)
        target = ReplacementTarget(
            device_id=device.id,
            hostname=device.hostname,
            vendor=device.vendor,
            model=device.model,
            site_name=device.site.name if device.site else None,
            reason=reason,
            due_on=due,
            severity=severity,
            estimated_cost=cost,
            impact_count=impacts.get(device.id, 0),
        )

        if due is None:
            plan.unscheduled.append(target)
            continue

        year = fiscal_year_of(due)
        # 既に過ぎているものは「今年度に対応すべきもの」として今年度へ寄せる
        year = max(year, start_year)
        bucket = buckets.get(year)
        if bucket is None:
            # 対象年度より先のものは計画の範囲外
            continue

        bucket.targets.append(target)
        bucket.device_count += 1
        bucket.estimated_cost += cost or 0.0

    for bucket in buckets.values():
        bucket.targets.sort(key=lambda t: (t.due_on or date.max, t.hostname))
        plan.years.append(bucket)
        plan.total_cost += bucket.estimated_cost
        plan.total_devices += bucket.device_count

    plan.years.sort(key=lambda y: y.fiscal_year)
    plan.unscheduled.sort(key=lambda t: t.hostname)
    return plan


# ---------------------------------------------------------------------------
# 影響範囲
# ---------------------------------------------------------------------------


def _adjacency(db: Session) -> dict[int, set[int]]:
    graph: dict[int, set[int]] = defaultdict(set)
    for link in db.query(Link).all():
        graph[link.a_device_id].add(link.b_device_id)
        graph[link.b_device_id].add(link.a_device_id)
    return graph


def _anchor(graph: dict[int, set[int]]) -> int | None:
    """基幹とみなすノード。最も多く繋がっている機器を選ぶ。"""
    if not graph:
        return None
    return max(graph, key=lambda node: (len(graph[node]), -node))


def _reachable(graph: dict[int, set[int]], start: int, blocked: int) -> set[int]:
    seen = {start}
    stack = [start]
    while stack:
        node = stack.pop()
        for neighbor in graph.get(node, ()):
            if neighbor == blocked or neighbor in seen:
                continue
            seen.add(neighbor)
            stack.append(neighbor)
    return seen


def isolated_by(db: Session, device_id: int) -> list[int]:
    """その機器を止めたとき、基幹から辿れなくなる機器のID。"""
    graph = _adjacency(db)
    if device_id not in graph:
        return []

    anchor = _anchor({k: v for k, v in graph.items() if k != device_id})
    if anchor is None or anchor == device_id:
        return []

    reachable = _reachable(graph, anchor, blocked=device_id)
    return sorted(node for node in graph if node != device_id and node not in reachable)


def impact_counts(db: Session) -> dict[int, int]:
    """全機器について、止めたときに経路が失われる機器の数。"""
    graph = _adjacency(db)
    return {device_id: len(isolated_by(db, device_id)) for device_id in graph}


@dataclass
class ImpactReport:
    device_id: int
    hostname: str
    directly_connected: list[str]  # 直接つながっている機器
    isolated: list[str]  # 経路が失われる機器
    note: str


def impact_of(db: Session, device_id: int) -> ImpactReport:
    device = db.get(Device, device_id)
    if device is None:
        raise ValueError("対象の機器が見つかりません")

    graph = _adjacency(db)
    names = {d.id: d.hostname for d in db.query(Device).all()}
    isolated = isolated_by(db, device_id)

    if device_id not in graph:
        note = (
            "この機器はトポロジ上で他の機器と繋がっていません。"
            "Config を取り込むと隣接情報から結線が推定されます"
        )
    elif isolated:
        note = f"{len(isolated)}台が経路を失います。更改の際は停止時間の調整と周知が必要です"
    else:
        note = "経路が失われる機器はありません（迂回経路があります）"

    return ImpactReport(
        device_id=device_id,
        hostname=device.hostname,
        directly_connected=sorted(names.get(n, f"#{n}") for n in graph.get(device_id, set())),
        isolated=[names.get(n, f"#{n}") for n in isolated],
        note=note,
    )


def severity_rank(value: str) -> int:
    order = [
        Severity.EXPIRED,
        Severity.CRITICAL,
        Severity.WARNING,
        Severity.UNKNOWN,
        Severity.OK,
    ]
    try:
        return order.index(Severity(value))
    except ValueError:
        return len(order)
