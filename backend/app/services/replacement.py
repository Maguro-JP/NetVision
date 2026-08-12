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

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus, Link, MaintenanceContract
from app.models.reference import ProductReference
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
    #: 年度の対象機器。多いときは頭から少しだけ（target_total が実数）
    targets: list[ReplacementTarget] = field(default_factory=list)
    target_total: int = 0


@dataclass
class ReplacementPlan:
    evaluated_on: date
    years: list[FiscalYearPlan] = field(default_factory=list)
    # 期日が分からず年度に割り付けられなかったもの（台帳の抜け）
    unscheduled: list[ReplacementTarget] = field(default_factory=list)
    #: 割り付けられなかったものの総数（unscheduled は途中で切っている）
    unscheduled_total: int = 0
    total_cost: float = 0.0
    total_devices: int = 0


def _due_and_reason(device: Device, today: date) -> tuple[date | None, str, str]:
    """更改の期日として何を見るか。

    保守期限と EoL のうち早い方が実質の期限になる。
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


#: 年度ごとに返す機器の数。全部返しても読めないし、16万台の環境では
#: 応答が数十MBになって受け取る側が固まる。件数と金額は全件から出す
_TARGET_SAMPLE = 200


def _due_expression(today: date):
    """機器ごとの更改期日を SQL で出す。

    保守期限（契約のうち最も遠い終了日）と EoL（機器の入力、無ければ型番の
    製品マスタ）のうち、早い方が実質の期限になる。判定の意味は
    _due_and_reason と同じで、こちらはその写し。

    以前は全機器を読み込んで1台ずつ評価していた。16万台では読み込みだけで
    13秒かかっており、年度ごとの台数を数えるだけでもその時間を払っていた。
    """
    maintenance_end = (
        select(func.max(MaintenanceContract.end_date))
        .where(MaintenanceContract.device_id == Device.id)
        .correlate(Device)
        .scalar_subquery()
    )
    product_eol = (
        select(ProductReference.eol_date)
        .where(
            func.lower(ProductReference.vendor) == func.lower(Device.vendor),
            func.lower(ProductReference.model) == func.lower(Device.model),
        )
        .correlate(Device)
        .limit(1)
        .scalar_subquery()
    )
    eol = func.coalesce(Device.eol_date, product_eol)
    # 早い方。片方しか無ければそちら
    return case(
        (maintenance_end.is_(None), eol),
        (eol.is_(None), maintenance_end),
        (maintenance_end < eol, maintenance_end),
        else_=eol,
    )


def _annual_cost_expression():
    """機器ごとの年間保守費。更改費用の概算はここから出す。"""
    return (
        select(func.coalesce(func.sum(MaintenanceContract.annual_cost), 0))
        .where(MaintenanceContract.device_id == Device.id)
        .correlate(Device)
        .scalar_subquery()
    )


#: 年度ごとに返す機器の数。全部返しても読めないし、16万台の環境では
#: 応答が数十MBになって受け取る側が固まる。件数と金額は全件から出す
_TARGET_SAMPLE = 200


def _fiscal_range(year: int) -> tuple[date, date]:
    """その年度の始まりと終わり（年度は4月始まり）。"""
    return date(year, FISCAL_START_MONTH, 1), date(year + 1, FISCAL_START_MONTH, 1)


def build_plan(
    db: Session, *, years: int = 5, today: date | None = None, keep: int | None = _TARGET_SAMPLE
) -> ReplacementPlan:
    """更改の年度計画を組み立てる。

    台数と金額は SQL の集計で出す。中身（対象機器の一覧）は年度ごとに
    `keep` 件までしか読まないので、台数が増えても速さと大きさが変わらない。
    """
    today = today or date.today()
    start_year = fiscal_year_of(today)
    last_year = start_year + years - 1

    due = _due_expression(today)
    cost = func.round(_annual_cost_expression() * 10, -3)
    alive = Device.status != DeviceStatus.RETIRED

    plan = ReplacementPlan(evaluated_on=today)

    for year in range(start_year, last_year + 1):
        begin, end = _fiscal_range(year)
        if year == start_year:
            # 既に過ぎているものは「今年度に対応すべきもの」として今年度へ寄せる
            window = due < end
        else:
            window = (due >= begin) & (due < end)

        row = db.execute(
            select(func.count(Device.id), func.coalesce(func.sum(cost), 0.0)).where(alive, window)
        ).one()
        bucket = FiscalYearPlan(
            fiscal_year=year,
            label=fiscal_year_label(year),
            device_count=row[0],
            estimated_cost=float(row[1] or 0.0),
            target_total=row[0],
        )

        if bucket.device_count:
            wanted = select(Device.id).where(alive, window).order_by(due, Device.hostname)
            if keep is not None:
                wanted = wanted.limit(keep)
            bucket.targets = _targets_for(db, list(db.scalars(wanted)), today)

        plan.years.append(bucket)
        plan.total_cost += bucket.estimated_cost
        plan.total_devices += bucket.device_count

    # 期日が分からず年度に割り付けられなかったもの（台帳の抜け）
    unscheduled = select(Device.id).where(alive, due.is_(None))
    plan.unscheduled_total = (
        db.scalar(select(func.count()).select_from(unscheduled.subquery())) or 0
    )
    if plan.unscheduled_total:
        listed = unscheduled.order_by(Device.hostname)
        if keep is not None:
            listed = listed.limit(keep)
        plan.unscheduled = _targets_for(db, list(db.scalars(listed)), today)

    return plan


def _targets_for(db: Session, device_ids: list[int], today: date) -> list[ReplacementTarget]:
    """一覧に出す分だけ、機器を読んで対象に組み立てる。

    影響台数もここでしか出さない。全機器分を求めると、経路の探索を
    台数だけ繰り返すことになる。
    """
    if not device_ids:
        return []

    devices = list(
        db.scalars(
            select(Device)
            .options(selectinload(Device.contracts), selectinload(Device.site))
            .where(Device.id.in_(device_ids))
        )
    )
    order = {device_id: index for index, device_id in enumerate(device_ids)}
    devices.sort(key=lambda d: order.get(d.id, len(order)))

    impacts = impact_counts(db, only=set(device_ids))

    targets: list[ReplacementTarget] = []
    for device in devices:
        found, reason, severity = _due_and_reason(device, today)
        targets.append(
            ReplacementTarget(
                device_id=device.id,
                hostname=device.hostname,
                vendor=device.vendor,
                model=device.model,
                site_name=device.site.name if device.site else None,
                reason=reason,
                due_on=found,
                severity=severity,
                estimated_cost=_estimated_cost(device),
                impact_count=impacts.get(device.id, 0),
            )
        )
    return targets


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


def impact_counts(db: Session, *, only: set[int] | None = None) -> dict[int, int]:
    """止めたときに経路が失われる機器の数。

    `only` を渡すと、その機器の分だけ求める。全機器分を求めると経路の探索を
    台数だけ繰り返すことになり、台数が増えたときに終わらない。
    """
    graph = _adjacency(db)
    wanted = graph.keys() if only is None else (graph.keys() & only)
    return {device_id: len(isolated_by(db, device_id)) for device_id in wanted}


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
