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

from sqlalchemy import Select, String, case, cast, func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.config import settings
from app.models import Device, DeviceStatus, DeviceType, Site
from app.models.maintenance import MaintenanceContract
from app.models.reference import ProductReference
from app.services import appsettings
from app.services.lifecycle import Severity

# 一覧の探す欄で日本語の呼び名でも当たるようにする。画面の表示名と同じ語。
_DEVICE_TYPE_SEARCH: dict[DeviceType, tuple[str, ...]] = {
    DeviceType.ROUTER: ("ルータ", "ルーター", "router"),
    DeviceType.SWITCH: ("スイッチ", "switch"),
    DeviceType.FIREWALL: ("ファイアウォール", "firewall"),
    DeviceType.LOAD_BALANCER: ("ロードバランサ", "ロードバランサー", "load_balancer"),
    DeviceType.WIRELESS_AP: ("無線ap", "無線AP", "wireless_ap"),
    DeviceType.WIRELESS_CONTROLLER: ("無線コントローラ", "wireless_controller"),
    DeviceType.SERVER: ("サーバ", "サーバー", "server"),
    DeviceType.OTHER: ("その他", "other"),
}
_DEVICE_STATUS_SEARCH: dict[DeviceStatus, tuple[str, ...]] = {
    DeviceStatus.ACTIVE: ("稼働中", "active"),
    DeviceStatus.STANDBY: ("予備機", "standby"),
    DeviceStatus.MAINTENANCE: ("保守作業中", "maintenance"),
    DeviceStatus.RETIRED: ("撤去済み", "retired"),
}
_SEVERITY_SEARCH: dict[Severity, tuple[str, ...]] = {
    Severity.EXPIRED: ("期限切れ", "expired"),
    Severity.CRITICAL: ("要対応", "critical"),
    Severity.WARNING: ("要計画", "warning"),
    Severity.OK: ("問題なし", "ok"),
    Severity.UNKNOWN: ("情報未登録", "unknown"),
}


@dataclass
class DeviceFilters:
    #: どの項目でも探す（部分一致）。列ごとの絞り込みとは別
    q: str | None = None
    #: 列ごとの部分一致。Excel のフィルター内検索と同じ使い方
    hostname: str | None = None
    vendor: str | None = None
    model: str | None = None
    serial_number: str | None = None
    os_version: str | None = None
    mgmt_ip: str | None = None
    rack: str | None = None
    notes: str | None = None
    site_name: str | None = None
    #: メーカーまたは型番のどちらかに含む
    product: str | None = None
    #: 保守期限（最も遠い終了日）の文字列部分一致。年や月で絞れる
    maintenance: str | None = None
    #: EoL 日付の文字列部分一致
    eol: str | None = None
    device_type: DeviceType | None = None
    status: DeviceStatus | None = None
    site_id: int | None = None
    severity: Severity | None = None
    #: 埋まっていない項目がある機器だけに絞る
    incomplete: bool = False


def _like(value: str) -> str:
    return f"%{value}%"


def _enums_matching(needle: str, mapping: dict) -> list:
    """探す語が呼び名や値に含まれる列挙だけを返す。"""
    folded = needle.casefold()
    hits = []
    for member, aliases in mapping.items():
        if any(folded in alias.casefold() for alias in aliases):
            hits.append(member)
    return hits


def _rank_of(column, today: date):
    """日付1つ分の重要度を、比較しやすい数（lifecycle の rank）に直す。"""
    return case(
        (column.is_(None), Severity.UNKNOWN.rank),
        (column < today, Severity.EXPIRED.rank),
        (
            column
            <= today + timedelta(days=appsettings.number("critical_days", settings.critical_days)),
            Severity.CRITICAL.rank,
        ),
        (
            column
            <= today + timedelta(days=appsettings.number("warning_days", settings.warning_days)),
            Severity.WARNING.rank,
        ),
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
    無ければ型番の製品マスター、という lifecycle.evaluate と同じ決め方。
    """
    maintenance_rank = _rank_of(_maintenance_end_expr(), today)
    eol_rank = _rank_of(_eol_expr(), today)
    worse = case((maintenance_rank > eol_rank, maintenance_rank), else_=eol_rank)

    # 撤去済みはアラート対象から外す
    return case((Device.status == DeviceStatus.RETIRED, Severity.OK.rank), else_=worse)


def build_query(filters: DeviceFilters, *, today: date | None = None) -> Select:
    """条件だけを組み立てる（並べ替えや件数の切り出しは呼ぶ側で）。"""
    stmt = select(Device)

    # 拠点名・住所で探す・絞るときだけ外部結合する。毎回結合すると無駄になる
    needs_site = bool(filters.q or filters.site_name)
    if needs_site:
        stmt = stmt.outerjoin(Site, Device.site_id == Site.id)

    if filters.q:
        like = _like(filters.q)
        text_hits = [
            Device.hostname.ilike(like),
            Device.code.ilike(like),
            Device.vendor.ilike(like),
            Device.model.ilike(like),
            Device.serial_number.ilike(like),
            Device.os_name.ilike(like),
            Device.os_version.ilike(like),
            Device.firmware_version.ilike(like),
            Device.rack.ilike(like),
            Device.rack_unit.ilike(like),
            Device.mgmt_ip.ilike(like),
            Device.notes.ilike(like),
            Site.name.ilike(like),
            Site.code.ilike(like),
            Site.address.ilike(like),
            cast(Device.install_date, String).ilike(like),
            cast(Device.eos_date, String).ilike(like),
            cast(Device.eol_date, String).ilike(like),
            cast(_maintenance_end_expr(), String).ilike(like),
        ]
        type_hits = _enums_matching(filters.q, _DEVICE_TYPE_SEARCH)
        if type_hits:
            text_hits.append(Device.device_type.in_(type_hits))
        status_hits = _enums_matching(filters.q, _DEVICE_STATUS_SEARCH)
        if status_hits:
            text_hits.append(Device.status.in_(status_hits))
        severity_hits = _enums_matching(filters.q, _SEVERITY_SEARCH)
        if severity_hits:
            ranks = [s.rank for s in severity_hits]
            text_hits.append(_overall_rank(today or date.today()).in_(ranks))
        stmt = stmt.where(or_(*text_hits))

    # 列フィルターはすべて部分一致。Excel で値の一部を打って絞るのと同じ
    if filters.hostname:
        stmt = stmt.where(Device.hostname.ilike(_like(filters.hostname)))
    if filters.vendor:
        stmt = stmt.where(Device.vendor.ilike(_like(filters.vendor)))
    if filters.model:
        stmt = stmt.where(Device.model.ilike(_like(filters.model)))
    if filters.serial_number:
        stmt = stmt.where(Device.serial_number.ilike(_like(filters.serial_number)))
    if filters.os_version:
        stmt = stmt.where(Device.os_version.ilike(_like(filters.os_version)))
    if filters.mgmt_ip:
        stmt = stmt.where(Device.mgmt_ip.ilike(_like(filters.mgmt_ip)))
    if filters.rack:
        stmt = stmt.where(
            or_(Device.rack.ilike(_like(filters.rack)), Device.rack_unit.ilike(_like(filters.rack)))
        )
    if filters.notes:
        stmt = stmt.where(Device.notes.ilike(_like(filters.notes)))
    if filters.product:
        like = _like(filters.product)
        stmt = stmt.where(or_(Device.vendor.ilike(like), Device.model.ilike(like)))
    if filters.site_name:
        stmt = stmt.where(Site.name.ilike(_like(filters.site_name)))
    if filters.maintenance:
        stmt = stmt.where(cast(_maintenance_end_expr(), String).ilike(_like(filters.maintenance)))
    if filters.eol:
        stmt = stmt.where(cast(_eol_expr(), String).ilike(_like(filters.eol)))

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
    """機器側の EoL も保守終了日も無い台数。製品マスターの EoL は見ない。

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
