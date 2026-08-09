"""VLAN 台帳の管理。

実機の Config から読める名前は 32 文字までだったり、機器ごとに揺れていたりする。
数十〜数百の VLAN を扱う現場では「10 は何のセグメントか」を人が書ける場所が要る。
ここはその**人が書く側**で、Config の取り込みでは一切上書きしない。

観測値（DeviceVlan）と分けてあるので、台帳と実機の名前が食い違っていれば
それ自体が「機器側の設定が古い」という情報になる。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DeviceVlan, Vlan


def list_vlans(db: Session) -> list[Vlan]:
    return list(db.scalars(select(Vlan).order_by(Vlan.vlan_id)))


def get(db: Session, vlan_id: int) -> Vlan | None:
    return db.scalars(select(Vlan).where(Vlan.vlan_id == vlan_id)).first()


def upsert(db: Session, vlan_id: int, values: dict[str, object]) -> Vlan:
    """台帳の1行を作る/更新する。番号は1つにつき1行なので upsert で扱う。"""
    row = get(db, vlan_id)
    if row is None:
        row = Vlan(vlan_id=vlan_id)
        db.add(row)
    for key, value in values.items():
        setattr(row, key, value)
    db.commit()
    db.refresh(row)
    return row


def delete(db: Session, vlan_id: int) -> bool:
    row = get(db, vlan_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


@dataclass
class ImportResult:
    created: int = 0
    updated: int = 0


def adopt_observed(db: Session, *, overwrite: bool = False) -> ImportResult:
    """実機に書いてある VLAN 名を台帳へ取り込む。

    台帳を1から手で埋めるのは現実的ではないので、**まず実機の名前で下書きを作り**、
    そこから用途を書き足していく想定。既に台帳にある行は既定では触らない
    （人が書いた内容を機器の設定で潰さないため）。
    """
    catalog = {row.vlan_id: row for row in db.scalars(select(Vlan))}
    observed: dict[int, str] = {}
    for row in db.scalars(select(DeviceVlan).order_by(DeviceVlan.device_id)):
        if row.name and row.vlan_id not in observed:
            observed[row.vlan_id] = row.name

    result = ImportResult()
    for vlan_id, name in observed.items():
        entry = catalog.get(vlan_id)
        if entry is None:
            db.add(Vlan(vlan_id=vlan_id, name=name))
            result.created += 1
        elif overwrite or not entry.name:
            entry.name = name
            result.updated += 1

    db.commit()
    return result
