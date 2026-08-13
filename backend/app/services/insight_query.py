"""指摘の件数を SQL で出す。ノートPCで全台を evaluate しないため。

中身（文言）は件数の多い種類から必要な分だけ機器を読んで作る。
件数と削減見込みは全件の SQL 集計なので、台数が増えても変わらない。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import case, exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.models import Device, DeviceStatus
from app.models.maintenance import MaintenanceContract
from app.models.reference import ProductReference
from app.services.device_query import _eol_expr, _maintenance_end_expr
from app.services.insights import (
    AGING_YEARS,
    KIND_META,
    Insight,
    InsightKind,
    KindSummary,
)


def _aging_min_days() -> int:
    """round(days/365.25, 1) が AGING_YEARS 以上になる最小の日数。"""
    days = 0
    while round(days / 365.25, 1) < AGING_YEARS:
        days += 1
    return days


def _scope(stmt, site_id: int | None):
    if site_id is not None:
        return stmt.where(Device.site_id == site_id)
    return stmt


def _live_end(today: date):
    return (
        select(func.max(MaintenanceContract.end_date))
        .where(
            MaintenanceContract.device_id == Device.id,
            MaintenanceContract.end_date >= today,
        )
        .correlate(Device)
        .scalar_subquery()
    )


def _live_cost(today: date):
    return (
        select(func.coalesce(func.sum(MaintenanceContract.annual_cost), 0.0))
        .where(
            MaintenanceContract.device_id == Device.id,
            MaintenanceContract.end_date >= today,
        )
        .correlate(Device)
        .scalar_subquery()
    )


def _has_live_contract(today: date):
    return exists(
        select(MaintenanceContract.id).where(
            MaintenanceContract.device_id == Device.id,
            MaintenanceContract.end_date >= today,
        )
    )


def _missing_product():
    matched = exists(
        select(ProductReference.id).where(
            func.lower(ProductReference.vendor) == func.lower(Device.vendor),
            func.lower(ProductReference.model) == func.lower(Device.model),
        )
    )
    return ~matched


def _cheaper(left, right):
    """2つの年額のうち安い方。SQLite / Postgres の方言に依存しない。"""
    a = func.coalesce(left, 0)
    b = func.coalesce(right, 0)
    return case((a <= b, a), else_=b)


@dataclass
class KindStat:
    kind: InsightKind
    count: int
    annual_saving: float
    sample_ids: list[int] = field(default_factory=list)


def _ids(db: Session, stmt, limit: int) -> list[int]:
    if limit <= 0:
        return []
    return list(db.scalars(stmt.limit(limit)))


def collect_kind_stats(
    db: Session, *, site_id: int | None, today: date, sample: int
) -> list[KindStat]:
    """種類ごとの件数・削減額と、文言を作るための機器 id。"""
    live_cost = _live_cost(today)
    live_end = _live_end(today)
    eol = _eol_expr()
    maintenance_end = _maintenance_end_expr()
    aging_before = today - timedelta(days=_aging_min_days())
    not_retired = Device.status != DeviceStatus.RETIRED
    stats: list[KindStat] = []

    def add(kind: InsightKind, count_stmt, saving_stmt, id_stmt) -> None:
        count = db.scalar(_scope(count_stmt, site_id)) or 0
        saving = float(db.scalar(_scope(saving_stmt, site_id)) or 0)
        ids = _ids(db, _scope(id_stmt, site_id), sample) if count else []
        stats.append(KindStat(kind=kind, count=int(count), annual_saving=saving, sample_ids=ids))

    add(
        InsightKind.CONTRACT_ON_RETIRED,
        select(func.count(Device.id)).where(
            Device.status == DeviceStatus.RETIRED, _has_live_contract(today)
        ),
        select(func.coalesce(func.sum(live_cost), 0.0)).where(
            Device.status == DeviceStatus.RETIRED, _has_live_contract(today)
        ),
        select(Device.id)
        .where(Device.status == DeviceStatus.RETIRED, _has_live_contract(today))
        .order_by(live_cost.desc(), Device.hostname),
    )

    add(
        InsightKind.CONTRACT_ON_STANDBY,
        select(func.count(Device.id)).where(Device.status == DeviceStatus.STANDBY, live_cost > 0),
        select(func.coalesce(func.sum(live_cost) / 2, 0.0)).where(
            Device.status == DeviceStatus.STANDBY, live_cost > 0
        ),
        select(Device.id)
        .where(Device.status == DeviceStatus.STANDBY, live_cost > 0)
        .order_by(live_cost.desc(), Device.hostname),
    )

    c1 = MaintenanceContract.__table__.alias("c1")
    c2 = MaintenanceContract.__table__.alias("c2")
    overlap_rows = (
        select(
            c1.c.device_id,
            _cheaper(c1.c.annual_cost, c2.c.annual_cost).label("pair_saving"),
        )
        .select_from(c1)
        .join(c2, (c1.c.device_id == c2.c.device_id) & (c1.c.id < c2.c.id))
        .join(Device, Device.id == c1.c.device_id)
        .where(
            c1.c.end_date >= today,
            c2.c.end_date >= today,
            c1.c.start_date <= c2.c.end_date,
            c2.c.start_date <= c1.c.end_date,
        )
    )
    if site_id is not None:
        overlap_rows = overlap_rows.where(Device.site_id == site_id)
    overlap_sub = overlap_rows.subquery()
    dup_count = db.scalar(select(func.count()).select_from(overlap_sub)) or 0
    dup_saving = float(
        db.scalar(select(func.coalesce(func.sum(overlap_sub.c.pair_saving), 0.0))) or 0
    )
    dup_ids = (
        list(db.scalars(select(overlap_sub.c.device_id).distinct().limit(sample)))
        if dup_count
        else []
    )
    stats.append(KindStat(InsightKind.DUPLICATE_CONTRACT, int(dup_count), dup_saving, dup_ids))

    add(
        InsightKind.CONTRACT_AFTER_EOL,
        select(func.count(Device.id)).where(
            eol.is_not(None),
            live_end.is_not(None),
            live_end > eol,
        ),
        select(func.coalesce(func.sum(0.0), 0.0)).where(
            eol.is_not(None), live_end.is_not(None), live_end > eol
        ),
        select(Device.id)
        .where(eol.is_not(None), live_end.is_not(None), live_end > eol)
        .order_by(Device.hostname),
    )

    add(
        InsightKind.ACTIVE_AFTER_EOL,
        select(func.count(Device.id)).where(Device.status == DeviceStatus.ACTIVE, eol < today),
        select(func.coalesce(func.sum(0.0), 0.0)).where(
            Device.status == DeviceStatus.ACTIVE, eol < today
        ),
        select(Device.id)
        .where(Device.status == DeviceStatus.ACTIVE, eol < today)
        .order_by(eol.asc(), Device.hostname),
    )

    add(
        InsightKind.ACTIVE_WITHOUT_CONTRACT,
        select(func.count(Device.id)).where(
            Device.status == DeviceStatus.ACTIVE, ~_has_live_contract(today)
        ),
        select(func.coalesce(func.sum(0.0), 0.0)).where(
            Device.status == DeviceStatus.ACTIVE, ~_has_live_contract(today)
        ),
        select(Device.id)
        .where(Device.status == DeviceStatus.ACTIVE, ~_has_live_contract(today))
        .order_by(Device.hostname),
    )

    add(
        InsightKind.AGING_DEVICE,
        select(func.count(Device.id)).where(
            Device.status == DeviceStatus.ACTIVE,
            Device.install_date.is_not(None),
            Device.install_date <= aging_before,
        ),
        select(func.coalesce(func.sum(0.0), 0.0)).where(
            Device.status == DeviceStatus.ACTIVE,
            Device.install_date.is_not(None),
            Device.install_date <= aging_before,
        ),
        select(Device.id)
        .where(
            Device.status == DeviceStatus.ACTIVE,
            Device.install_date.is_not(None),
            Device.install_date <= aging_before,
        )
        .order_by(Device.install_date.asc(), Device.hostname),
    )

    add(
        InsightKind.MISSING_PRODUCT_REFERENCE,
        select(func.count(Device.id)).where(not_retired, _missing_product()),
        select(func.coalesce(func.sum(0.0), 0.0)).where(not_retired, _missing_product()),
        select(Device.id).where(not_retired, _missing_product()).order_by(Device.hostname),
    )

    add(
        InsightKind.MISSING_OS_VERSION,
        select(func.count(Device.id)).where(
            not_retired, or_(Device.os_version.is_(None), Device.os_version == "")
        ),
        select(func.coalesce(func.sum(0.0), 0.0)).where(
            not_retired, or_(Device.os_version.is_(None), Device.os_version == "")
        ),
        select(Device.id)
        .where(not_retired, or_(Device.os_version.is_(None), Device.os_version == ""))
        .order_by(Device.hostname),
    )

    add(
        InsightKind.MISSING_LIFECYCLE_DATA,
        select(func.count(Device.id)).where(not_retired, eol.is_(None), maintenance_end.is_(None)),
        select(func.coalesce(func.sum(0.0), 0.0)).where(
            not_retired, eol.is_(None), maintenance_end.is_(None)
        ),
        select(Device.id)
        .where(not_retired, eol.is_(None), maintenance_end.is_(None))
        .order_by(Device.hostname),
    )

    add(
        InsightKind.MISSING_SITE,
        select(func.count(Device.id)).where(not_retired, Device.site_id.is_(None)),
        select(func.coalesce(func.sum(0.0), 0.0)).where(not_retired, Device.site_id.is_(None)),
        select(Device.id).where(not_retired, Device.site_id.is_(None)).order_by(Device.hostname),
    )

    return stats


def mixed_provider_insights(db: Session, *, site_id: int | None, today: date) -> list[Insight]:
    """同じ型番で保守ベンダーが分かれているものを SQL で集約する。"""
    stmt = (
        select(Device.model, MaintenanceContract.provider, func.count(func.distinct(Device.id)))
        .join(MaintenanceContract, MaintenanceContract.device_id == Device.id)
        .where(Device.status != DeviceStatus.RETIRED, MaintenanceContract.end_date >= today)
        .group_by(Device.model, MaintenanceContract.provider)
    )
    if site_id is not None:
        stmt = stmt.where(Device.site_id == site_id)

    by_model: dict[str, dict[str, int]] = {}
    for model, provider, count in db.execute(stmt):
        by_model.setdefault(model, {})[provider] = count

    insights: list[Insight] = []
    category, title, severity = KIND_META[InsightKind.MIXED_PROVIDERS]
    for model, providers in by_model.items():
        if len(providers) < 2:
            continue
        detail = " / ".join(f"{name}: {count}台" for name, count in sorted(providers.items()))
        insights.append(
            Insight(
                kind=InsightKind.MIXED_PROVIDERS,
                category=category,
                title=title,
                severity=severity,
                detail=f"{model}: {detail}",
                recommendation="契約をまとめると単価と管理の両方で有利になる可能性があります。",
            )
        )
    return insights


def load_devices(db: Session, ids: list[int]) -> list[Device]:
    if not ids:
        return []
    found = {
        d.id: d
        for d in db.scalars(
            select(Device)
            .where(Device.id.in_(ids))
            .options(selectinload(Device.contracts), selectinload(Device.site))
        )
    }
    return [found[i] for i in ids if i in found]


def summaries_from_stats(
    stats: list[KindStat], extra: list[KindSummary] | None = None
) -> list[KindSummary]:
    out: list[KindSummary] = []
    for stat in stats:
        if stat.count <= 0:
            continue
        category, title, severity = KIND_META[stat.kind]
        out.append(
            KindSummary(
                kind=stat.kind,
                category=category,
                title=title,
                severity=severity,
                count=stat.count,
                annual_saving=stat.annual_saving,
            )
        )
    if extra:
        out.extend(extra)
    out.sort(key=lambda s: (-s.severity.rank, -s.annual_saving, -s.count))
    return out
