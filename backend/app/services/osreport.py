"""OS の版が、どれだけ散らばっているか。

同じ機種なのに版がばらばらだと、同じ操作が同じ結果にならず、不具合の
当たりも付けられません。更改や脆弱性の話も、まず「いま何がどれだけ動いて
いるか」が分からないと始まりません。

数えるだけなので、答えは一つに決まります。判断（上げるべきかどうか）は
しません。上げられない事情は現場にあり、台帳からは読めないためです。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device
from app.services import versions


@dataclass
class Running:
    """ある機種で動いている版1つ。"""

    version: str
    count: int
    hostnames: list[str] = field(default_factory=list)
    #: その機種の中でいちばん新しい版か
    newest: bool = False


@dataclass
class ModelSpread:
    """機種1つぶんの散らばり。"""

    model: str
    os_name: str
    devices: int
    versions: list[Running] = field(default_factory=list)
    newest: str = ""
    behind: int = 0
    unknown: int = 0
    basis: str = ""

    @property
    def mixed(self) -> bool:
        return len([v for v in self.versions if v.version]) > 1


@dataclass
class OsReport:
    models: list[ModelSpread] = field(default_factory=list)
    #: 版が入っていない機器
    unknown_total: int = 0
    devices: int = 0
    notes: list[str] = field(default_factory=list)


def review(db: Session) -> OsReport:
    """機種ごとに、動いている版と、遅れている台数を出す。"""
    rows = db.execute(
        select(Device.hostname, Device.model, Device.os_name, Device.os_version)
    ).all()

    groups: dict[tuple[str, str], list[tuple[str, str | None]]] = defaultdict(list)
    for hostname, model, os_name, os_version in rows:
        key = (model or "（型番が未記入）", versions.normalize_name(os_name) or "")
        groups[key].append((hostname, os_version))

    report = OsReport(devices=len(rows))
    for (model, os_key), members in groups.items():
        spread = _spread(model, os_key, members)
        report.unknown_total += spread.unknown
        report.models.append(spread)

    # 揃っていないものから先に見せる。下まで読まないと気づかない、を避ける
    report.models.sort(key=lambda m: (not m.mixed, -m.behind, m.model))

    if not rows:
        report.notes.append("機器が台帳にありません")
    else:
        mixed = [m for m in report.models if m.mixed]
        if mixed:
            report.notes.append(
                f"同じ機種で版が揃っていないものが {len(mixed)} 機種あります。"
                "同じ操作が同じ結果になるとは限りません"
            )
        if report.unknown_total:
            report.notes.append(
                f"版が入っていない機器が {report.unknown_total} 台あります。"
                "Config を取り込むと埋まります"
            )
    return report


def _spread(model: str, os_key: str, members: list[tuple[str, str | None]]) -> ModelSpread:
    counts: dict[str, list[str]] = defaultdict(list)
    unknown = 0
    for hostname, version in members:
        if not version:
            unknown += 1
            continue
        counts[version].append(hostname)

    running = [
        Running(version=version, count=len(hosts), hostnames=sorted(hosts)[:20])
        for version, hosts in counts.items()
    ]
    newest = ""
    for item in running:
        if not newest or versions.compare(item.version, newest) > 0:
            newest = item.version
    for item in running:
        item.newest = bool(newest) and versions.compare(item.version, newest) == 0

    running.sort(key=lambda r: (not r.newest, -r.count, r.version))
    behind = sum(item.count for item in running if not item.newest)

    spread = ModelSpread(
        model=model,
        os_name=os_key,
        devices=len(members),
        versions=running,
        newest=newest,
        behind=behind,
        unknown=unknown,
    )
    if newest:
        spread.basis = (
            f"この機種でいちばん新しいのは {newest} で、それより古い版のままなのが"
            f" {behind} 台です（版の新旧は、数字と記号を順に比べて決めています）"
        )
    else:
        spread.basis = "版が1つも入っていないので、比べられません"
    return spread
