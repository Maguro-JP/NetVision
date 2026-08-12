"""変更を戻す。

手で台帳を直していく作業は、戻せないと踏み切れない。「間違えたら終わり」
だと、直すべきところも触れなくなる。

戻せるのは、変更履歴に残っている「項目の書き換え」だけ。作成や削除は
戻さない（削除された機器を履歴から復元しても、結線や取り込み履歴までは
戻らず、中途半端な機器が生えるため）。戻せないものは、なぜ戻せないかを
返す。

取り込み1回分をまとめて戻すこともできる。台帳を丸ごと取り込んで結果が
おかしかったとき、1件ずつ戻すのは現実的ではないため。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Device, Site
from app.models.change_log import ChangeAction, ChangeLog

#: 戻せる項目。履歴に残る名前と、機器の属性名は同じ
REVERTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "hostname",
        "vendor",
        "model",
        "serial_number",
        "device_type",
        "status",
        "os_name",
        "os_version",
        "firmware_version",
        "site_id",
        "rack",
        "rack_unit",
        "mgmt_ip",
        "install_date",
        "eos_date",
        "eol_date",
        "notes",
    }
)

_DATE_FIELDS = frozenset({"install_date", "eos_date", "eol_date"})


class NotRevertable(Exception):
    """戻せない理由を持った失敗。画面にそのまま出す。"""


@dataclass
class RevertResult:
    reverted: int = 0
    #: 戻さなかった履歴と、その理由
    skipped: list[tuple[int, str]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.skipped is None:
            self.skipped = []


def _parse(field: str, text: str | None):
    """履歴に残っている文字列を、機器に入れられる値へ戻す。"""
    if text is None or text == "":
        return None
    if field in _DATE_FIELDS:
        return date.fromisoformat(text)
    if field == "site_id":
        return int(text)
    return text


def _check(entry: ChangeLog) -> str | None:
    """戻せない理由。戻せるなら None。"""
    if entry.entity_type != "device":
        return "機器の変更ではありません"
    if entry.action is not ChangeAction.UPDATE:
        return "作成・削除は戻せません"
    if entry.field is None or entry.field not in REVERTABLE_FIELDS:
        return f"「{entry.field}」は戻せない項目です"
    if entry.entity_id is None:
        return "対象の機器が分かりません"
    return None


def _apply(db: Session, entry: ChangeLog) -> str | None:
    """1件戻す。戻せなければ理由を返す。"""
    reason = _check(entry)
    if reason is not None:
        return reason

    device = db.get(Device, entry.entity_id)
    if device is None:
        return "対象の機器は台帳にありません"

    try:
        value = _parse(entry.field or "", entry.old_value)
    except ValueError:
        return f"記録されている値「{entry.old_value}」を戻せませんでした"

    if entry.field == "site_id" and value is not None and db.get(Site, value) is None:
        return "戻し先の拠点が台帳にありません"

    if entry.field == "hostname" and value is not None:
        taken = db.scalar(select(Device).where(Device.hostname == value))
        if taken is not None and taken.id != device.id:
            return f"ホスト名「{value}」は他の機器が使っています"

    setattr(device, entry.field, value)
    return None


def revert_entry(db: Session, entry_id: int) -> None:
    """変更履歴1件を戻す。戻せない場合は NotRevertable。"""
    entry = db.get(ChangeLog, entry_id)
    if entry is None:
        raise NotRevertable("その変更履歴は見つかりません")

    reason = _apply(db, entry)
    if reason is not None:
        raise NotRevertable(reason)
    db.commit()


def revert_batch(db: Session, batch_id: str) -> RevertResult:
    """取り込み1回分をまとめて戻す。

    新しいものから順に戻す。同じ項目が2回書き換わっていた場合、古い方の
    「変更前」まで戻したいので、逆順に当てる必要がある。
    """
    entries = list(
        db.scalars(
            select(ChangeLog).where(ChangeLog.batch_id == batch_id).order_by(ChangeLog.id.desc())
        )
    )
    if not entries:
        raise NotRevertable("その取り込みの履歴が見つかりません")

    result = RevertResult()
    for entry in entries:
        reason = _apply(db, entry)
        if reason is None:
            result.reverted += 1
        else:
            result.skipped.append((entry.id, reason))

    db.commit()
    return result
