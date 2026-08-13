"""機器の絞り込み条件。一覧APIとエクスポートAPIで同じ条件を使えるようにまとめている。

絞り込みも並べ替えも件数も、すべて SQL 側で行う。以前はいったん全件を
読み込んでからアプリ側で切っていた。数千台までなら通用したが、実運用の
16万台では 50 件を表示するだけで 10 秒かかっていた（全件の ORM 化に
その時間が消える）。SQL で切れば同じ 50 件が 13ms で返る。

重要度（保守期限・EoL のアラート）だけは日付から算出するため、素直には
SQL で書けない。ただし判定は「今日」「critical の境目」「warning の境目」
という3つの日付との大小比較でしかないので、境目の日付を Python で先に
出しておけば SQL の CASE 式で表せる。DB の日付関数に頼らないので、
SQLite でも Postgres でも同じ結果になる。

判定の意味は lifecycle.evaluate が正で、ここはその写しにあたる。
両者が食い違うと「一覧に出ないのに開くと期限切れ」という状態になるため、
全パターンで一致することを試験で見張っている（tests/test_device_query.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import Select, case, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Device, DeviceStatus, DeviceType
from app.models.maintenance import MaintenanceContract
from app.models.reference import ProductReference
from app.services.lifecycle import Severity


@dataclass
class DeviceFilters:
    q: str | None = None
    vendor: str | None = None
    device_type: DeviceType | None = None
    status: DeviceStatus | None = None
    site_id: int | None = None
    severity: Severity | None = None
    #: 埋まっていない項目がある機器だけに絞る
    incomplete: bool = False


def _rank_of(column, today: date):
    """日付1つ分の重要度を、比較しやすい数（lifecycle の rank）に直す。"""
    return case(
        (column.is_(None), Severity.UNKNOWN.rank),
        (column < today, Severity.EXPIRED.rank),
        (column <= today + timedelta(days=settings.critical_days), Severity.CRITICAL.rank),
        (column <= today + timedelta(days=settings.warning_days), Severity.WARNING.rank),
        else_=Severity.OK.rank,
    )


def _maintenance_end_expr():
    return (
        select(func.max(MaintenanceContract.end_date))
        .where(MaintenanceContract.device_id == Device.id)
        .correlate(Device)
        .scalar_subquery()
    )


def _eol_expr():
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
    return func.coalesce(Device.eol_date, product_eol)


def _soonest_date():
    """保守期限と EoL のうち早い方。ダッシュボードの「期限が近い順」に使う。"""
    maintenance_end = _maintenance_end_expr()
    eol = _eol_expr()
    return case(
        (maintenance_end.is_(None), eol),
        (eol.is_(None), maintenance_end),
        (maintenance_end < eol, maintenance_end),
        else_=eol,
    )


def _overall_rank(today: date):
    """機器1台の総合重要度。保守期限と EoL の重い方を採る。

    保守期限は契約のうち最も遠い終了日、EoL は機器個別の入力を優先して
    無ければ型番の製品マスタ、という lifecycle.evaluate と同じ決め方。
    """
    maintenance_rank = _rank_of(_maintenance_end_expr(), today)
    eol_rank = _rank_of(_eol_expr(), today)
    worse = case((maintenance_rank > eol_rank, maintenance_rank), else_=eol_rank)

    # 撤去済みはアラート対象から外す
    return case((Device.status == DeviceStatus.RETIRED, Severity.OK.rank), else_=worse)


def build_query(filters: DeviceFilters, *, today: date | None = None) -> Select:
    """条件だけを組み立てる（並べ替えや件数の切り出しは呼ぶ側で）。"""
    stmt = select(Device)

    if filters.q:
        like = f"%{filters.q}%"
        stmt = stmt.where(
            or_(
                Device.hostname.ilike(like),
                Device.model.ilike(like),
                Device.serial_number.ilike(like),
            )
        )
    if filters.vendor:
        stmt = stmt.where(Device.vendor == filters.vendor)
    if filters.device_type:
        stmt = stmt.where(Device.device_type == filters.device_type)
    if filters.status:
        stmt = stmt.where(Device.status == filters.status)
    if filters.site_id is not None:
        stmt = stmt.where(Device.site_id == filters.site_id)
    if filters.incomplete:
        # Device.REQUIRED_FIELDS と同じ項目。空文字と NULL の両方が入りうる
        stmt = stmt.where(
            or_(
                Device.vendor.is_(None),
                Device.vendor == "",
                Device.model.is_(None),
                Device.model == "",
                Device.site_id.is_(None),
            )
        )
    if filters.severity is not None:
        stmt = stmt.where(_overall_rank(today or date.today()) == filters.severity.rank)

    return stmt


def count_devices(db: Session, filters: DeviceFilters, *, today: date | None = None) -> int:
    """条件に合う総数。ページを送るために要る。"""
    inner = build_query(filters, today=today).with_only_columns(Device.id).order_by(None)
    return db.scalar(select(func.count()).select_from(inner.subquery())) or 0


def query_devices(
    db: Session,
    filters: DeviceFilters,
    *,
    limit: int | None = None,
    offset: int = 0,
    today: date | None = None,
) -> list[Device]:
    """条件に合う機器を、必要な分だけ読む。

    limit を渡さないと全件読む。書き出しのように全件が要る用途のためだが、
    件数が多いと時間もメモリも相応にかかる。画面向けには必ず limit を渡すこと。
    """
    stmt = (
        build_query(filters, today=today)
        .options(selectinload(Device.contracts), selectinload(Device.site))
        .order_by(Device.hostname)
    )
    if offset:
        stmt = stmt.offset(offset)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def iter_devices(
    db: Session, filters: DeviceFilters, *, today: date | None = None, chunk: int = 200
):
    """全件を一度に ORM 化しない。指摘の洗い出しなど、1台ずつ見る処理向け。

    16万台を list に積むと、画面が固まるほどの待ちになる。
    selectinload と yield_per は相性が悪いので、id 順に塊で読む。
    """
    last_id = 0
    while True:
        stmt = (
            build_query(filters, today=today)
            .where(Device.id > last_id)
            .options(selectinload(Device.contracts), selectinload(Device.site))
            .order_by(Device.id)
            .limit(chunk)
        )
        batch = list(db.scalars(stmt))
        if not batch:
            return
        yield from batch
        last_id = batch[-1].id


def _counts_from_ranks(rows: list[tuple]) -> dict[str, int]:
    by_rank = {severity.rank: severity.value for severity in Severity}
    out = {severity.value: 0 for severity in Severity}
    for rank, count in rows:
        name = by_rank.get(int(rank or 0))
        if name:
            out[name] += count
    return out


def count_by_overall_severity(db: Session, *, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    rank = _overall_rank(today)
    rows = db.execute(select(rank, func.count()).group_by(rank)).all()
    return _counts_from_ranks(rows)


def count_by_maintenance_severity(db: Session, *, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    rank = _rank_of(_maintenance_end_expr(), today)
    rows = db.execute(select(rank, func.count()).group_by(rank)).all()
    return _counts_from_ranks(rows)


def count_by_eol_severity(db: Session, *, today: date | None = None) -> dict[str, int]:
    today = today or date.today()
    rank = _rank_of(_eol_expr(), today)
    rows = db.execute(select(rank, func.count()).group_by(rank)).all()
    return _counts_from_ranks(rows)


def count_by_vendor(db: Session) -> list[tuple[str, int]]:
    rows = db.execute(
        select(Device.vendor, func.count()).group_by(Device.vendor).order_by(func.count().desc())
    ).all()
    return [(vendor, count) for vendor, count in rows]


def count_without_lifecycle_data(db: Session) -> int:
    """機器側の EoL も保守終了日も無い台数。製品マスタの EoL は見ない。

    ダッシュボードの「ライフサイクル情報未登録」と同じ定義。
    """
    return (
        db.scalar(
            select(func.count(Device.id)).where(
                Device.eol_date.is_(None),
                _maintenance_end_expr().is_(None),
            )
        )
        or 0
    )


_ALERT_RANKS = (Severity.EXPIRED.rank, Severity.CRITICAL.rank, Severity.WARNING.rank)


def query_upcoming(db: Session, limit: int, *, today: date | None = None) -> list[Device]:
    """期限が近い機器。ダッシュボード用に、必要な件数だけ読む。"""
    today = today or date.today()
    stmt = (
        select(Device)
        .where(_overall_rank(today).in_(_ALERT_RANKS))
        .options(selectinload(Device.contracts), selectinload(Device.site))
        .order_by(_soonest_date().asc(), Device.hostname)
        .limit(limit)
    )
    return list(db.scalars(stmt))


def query_alerts(db: Session, within_days: int, *, today: date | None = None) -> list[Device]:
    """指定日数以内に保守切れ / EoL を迎える稼働機器。更改計画の材料。"""
    today = today or date.today()
    due = _soonest_date()
    stmt = (
        select(Device)
        .where(Device.status != DeviceStatus.RETIRED)
        .where(due.is_not(None))
        .where(due <= today + timedelta(days=within_days))
        .options(selectinload(Device.contracts), selectinload(Device.site))
        .order_by(due.asc(), Device.hostname)
    )
    return list(db.scalars(stmt))
