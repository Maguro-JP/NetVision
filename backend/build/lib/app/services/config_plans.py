"""現行フル / 将来フル / 差分 を組み立てる。

3つとも使い道が違うので、どれも「そのまま渡せる形」で出す。

- 現行フル … 機器故障時の復旧。取り込み済みの running-config をそのまま
- 将来フル … 更改で入る新機種に投入する。plan の本文をそのまま
- 差分   … 更改に合わせて既存機器に当てる。**将来フルと同じ状態になることを
            検証してからでないと出さない**

どれも生成するだけで、実機には投入しない。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ConfigPlan, ConfigSnapshot, Device
from app.services import config_render
from app.services.configdiff import DiffResult, build
from app.services.configparse.base import Vendor

_VENDOR_HINTS: list[tuple[tuple[str, ...], Vendor]] = [
    (("cisco",), Vendor.CISCO_IOS),
    (("juniper", "junos"), Vendor.JUNIPER),
    (("fortinet", "fortigate"), Vendor.FORTINET),
]


def vendor_from_name(name: str | None) -> str:
    """台帳のベンダー名から、差分で使う書式を推測する。"""
    lowered = (name or "").lower()
    for needles, vendor in _VENDOR_HINTS:
        if any(needle in lowered for needle in needles):
            return str(vendor)
    return str(Vendor.UNKNOWN)


def latest_snapshot(db: Session, device_id: int) -> ConfigSnapshot | None:
    """その機器の最新の取り込み済み config。"""
    return db.scalars(
        select(ConfigSnapshot)
        .where(ConfigSnapshot.device_id == device_id)
        .order_by(ConfigSnapshot.created_at.desc(), ConfigSnapshot.id.desc())
        .limit(1)
    ).first()


def snapshot_by_id(db: Session, snapshot_id: int | None) -> ConfigSnapshot | None:
    if snapshot_id is None:
        return None
    return db.get(ConfigSnapshot, snapshot_id)


@dataclass
class PlanOutputs:
    """plan から出せる3つの成果物。"""

    current_full: str
    future_full: str
    diff: DiffResult
    vendor: str
    base_snapshot_id: int | None
    base_is_stale: bool  # 基準にした現行より新しい config が取り込まれている
    notes: list[str] = field(default_factory=list)


def outputs(db: Session, plan: ConfigPlan) -> PlanOutputs:
    device = db.get(Device, plan.device_id)
    base = snapshot_by_id(db, plan.base_snapshot_id) or latest_snapshot(db, plan.device_id)
    newest = latest_snapshot(db, plan.device_id)

    current_full = base.content if base else ""
    # 明示 > 取り込んだ config の判定結果 > 台帳のベンダー名からの推測
    vendor = plan.vendor or (base.vendor if base and base.vendor else None)
    if not vendor:
        vendor = vendor_from_name(device.vendor if device else None)

    notes: list[str] = []
    if base is None:
        notes.append(
            "この機器の config がまだ取り込まれていないため、現行フルと差分は出せません。"
            "「Config取り込み」から running-config を登録してください"
        )

    stale = bool(base and newest and newest.id != base.id)
    if stale:
        notes.append(
            "基準にした現行 config より新しいものが取り込まれています。"
            "差分がずれるので、基準を最新にして作り直してください"
        )

    diff = build(current_full, plan.body, vendor)
    return PlanOutputs(
        current_full=current_full,
        future_full=plan.body,
        diff=diff,
        vendor=vendor,
        base_snapshot_id=base.id if base else None,
        base_is_stale=stale,
        notes=notes,
    )


def seed_body(db: Session, device: Device, source: str, template_id: int | None) -> str:
    """将来フルの初期値を作る。

    まっさらから書くことはまず無いので、現行をコピーして直すのを既定にする。
    """
    if source == "current":
        snapshot = latest_snapshot(db, device.id)
        return snapshot.content if snapshot else ""

    if source == "template" and template_id is not None:
        from app.models import ConfigTemplate

        template = db.get(ConfigTemplate, template_id)
        if template is None:
            raise ValueError("指定されたテンプレートが見つかりません")
        rendered = config_render.render(db, template, device, {})
        return rendered.rendered

    return ""
