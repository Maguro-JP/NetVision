"""眺めるための、拠点単位にまとめた network の姿。

Space（裏の可視化ページ）が使っていた全体グラフは、機器の数だけ大きくなる。
16万台の環境では取得だけで十数秒かかり、受け取っても1点ずつ描けるはずがない。
そもそも引いて眺めているときに見えているのは拠点の塊であって、機器1台ずつでは
ないので、拠点の単位まで先にまとめてから渡す。

返る大きさは拠点数で決まり、機器が何台あっても変わらない。
機器1台ずつを見たいときは、拠点を選んでその拠点だけを取りに行く。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Device, Site
from app.models.topology import Link
from app.services.device_query import _overall_rank
from app.services.lifecycle import Severity

#: 重要度の rank から名前へ戻す表
_BY_RANK = {severity.rank: severity for severity in Severity}


@dataclass
class SitePoint:
    site_id: int | None
    name: str
    address: str | None
    device_count: int
    #: その拠点で一番重い状態。まとめたせいで見落とさないため
    severity: str


@dataclass
class SiteEdge:
    a: int | None
    b: int | None
    #: 2拠点間に何本の結線があるか。太さに使う
    count: int


@dataclass
class SpaceView:
    sites: list[SitePoint] = field(default_factory=list)
    edges: list[SiteEdge] = field(default_factory=list)
    device_total: int = 0
    link_total: int = 0


def build(db: Session, *, today: date | None = None) -> SpaceView:
    """拠点ごとの点と、拠点間の線を組み立てる。"""
    today = today or date.today()
    rank = _overall_rank(today)

    rows = db.execute(
        select(
            Device.site_id,
            func.count(Device.id),
            func.max(rank),
        ).group_by(Device.site_id)
    ).all()

    names = {site.id: site for site in db.scalars(select(Site))}
    view = SpaceView()

    for site_id, count, worst in rows:
        site = names.get(site_id) if site_id is not None else None
        view.sites.append(
            SitePoint(
                site_id=site_id,
                name=site.name if site else "拠点未登録",
                address=site.address if site else None,
                device_count=count,
                severity=str(_BY_RANK.get(int(worst or 0), Severity.OK)),
            )
        )
        view.device_total += count

    view.sites.sort(key=lambda point: (point.site_id is None, point.name))

    # 拠点をまたぐ結線だけ数える。拠点の中の線は、引いて見ているときは見えない
    a_site = select(Device.site_id).where(Device.id == Link.a_device_id).scalar_subquery()
    b_site = select(Device.site_id).where(Device.id == Link.b_device_id).scalar_subquery()
    pairs = db.execute(select(a_site, b_site, func.count(Link.id)).group_by(a_site, b_site)).all()

    merged: dict[tuple[int | None, int | None], int] = {}
    for first, second, count in pairs:
        if first == second:
            continue
        key = (first, second) if (first or 0) <= (second or 0) else (second, first)
        merged[key] = merged.get(key, 0) + count

    view.edges = [SiteEdge(a=a, b=b, count=count) for (a, b), count in merged.items()]
    view.link_total = db.scalar(select(func.count()).select_from(Link)) or 0
    return view
