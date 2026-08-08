from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ExposureStatus(enum.StrEnum):
    OPEN = "open"  # 未対応
    IN_PROGRESS = "in_progress"  # 対応中
    MITIGATED = "mitigated"  # 暫定対処済み（回避策）。該当自体は残る
    FIXED = "fixed"  # 恒久対処済み（修正版へ上げた）
    ACCEPTED = "accepted"  # リスクを受容（理由必須）
    NOT_AFFECTED = "not_affected"  # 構成上そもそも影響しない
    RESOLVED = "resolved"  # 該当しなくなった（自動）


# 「まだ手を打っていない」とみなす状態
UNHANDLED = {ExposureStatus.OPEN, ExposureStatus.IN_PROGRESS}


class DeviceExposure(Base):
    """機器1台 × 脆弱性1件の対処状況。

    対処を「書いた」ことと、その機器に「実際に入れた」ことは別なので分けて持つ。
    書いただけで済んだ扱いになると、一番危ない見落とし方をする。

    恒久対処（修正版へ上げる）と暫定対処（設定で回避）も分ける。
    回避策を当てても機器のバージョンは変わらないので**該当し続ける**。
    ここを同じ「済み」にすると、直っていない機器が永久に見えなくなる。
    """

    __tablename__ = "device_exposures"
    __table_args__ = (
        UniqueConstraint("device_id", "vulnerability_id", name="uq_exposure_device_vuln"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    vulnerability_id: Mapped[int] = mapped_column(
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"), index=True
    )

    status: Mapped[ExposureStatus] = mapped_column(
        Enum(ExposureStatus, native_enum=False, length=16),
        default=ExposureStatus.OPEN,
        index=True,
    )
    # 適用した対処（あれば）
    remediation_id: Mapped[int | None] = mapped_column(
        ForeignKey("remediations.id", ondelete="SET NULL"), default=None, index=True
    )

    decided_by: Mapped[str | None] = mapped_column(String(120), default=None)
    decided_on: Mapped[date | None] = mapped_column(Date, default=None)
    note: Mapped[str | None] = mapped_column(Text, default=None)

    # 判定した時点の機器の状態。ここが変わったら判断をやり直す必要がある
    decided_os_version: Mapped[str | None] = mapped_column(String(80), default=None)
    # 現行 config に対処の設定が入っているか、最後に確認した結果
    config_verified_on: Mapped[date | None] = mapped_column(Date, default=None)
    config_verified: Mapped[bool | None] = mapped_column(default=None)

    # 状況が変わって差し戻したときの記録
    reopened_on: Mapped[date | None] = mapped_column(Date, default=None)
    reopen_reason: Mapped[str | None] = mapped_column(Text, default=None)

    first_seen_on: Mapped[date | None] = mapped_column(Date, default=None, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    device: Mapped[Device] = relationship()  # noqa: F821
    vulnerability: Mapped[Vulnerability] = relationship()  # noqa: F821
    remediation: Mapped[Remediation | None] = relationship()  # noqa: F821
