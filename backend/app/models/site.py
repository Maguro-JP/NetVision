from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class SiteStatus(enum.StrEnum):
    """拠点がいまどういう状態か。

    機器を消さずに「この拠点はいま使っていない」と言えるようにする。
    移転は住所を書き換えるだけで済むが、閉鎖・閉店は残った機器の
    引き上げが要るので、状態として持たせて数えられるようにする。
    """

    ACTIVE = "active"  # 稼働中
    PLANNED = "planned"  # 開設予定（工事中・準備中）
    SUSPENDED = "suspended"  # 一時閉鎖（休業・長期休止）
    CLOSED = "closed"  # 閉鎖・閉店（撤収済み、または撤収中）


class Site(Base):
    """機器の設置拠点（データセンター / 事業所 / フロアなど）。"""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 人が決める拠点ID。プロジェクト既定の番号をそのまま使える。空は未設定
    code: Mapped[str | None] = mapped_column(String(80), unique=True, default=None, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(String(255), default=None)
    #: いまどういう状態か。閉鎖しても機器は残す（引き上げの棚卸しに要る）
    status: Mapped[str] = mapped_column(
        String(20), default=SiteStatus.ACTIVE, server_default="active", index=True
    )
    #: 開いた日・閉じた日。いつ閉めたのかが分からないと棚卸しができない
    opened_on: Mapped[date | None] = mapped_column(Date, default=None)
    closed_on: Mapped[date | None] = mapped_column(Date, default=None)
    notes: Mapped[str | None] = mapped_column(String(1000), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    devices: Mapped[list[Device]] = relationship(  # noqa: F821
        back_populates="site", cascade="save-update"
    )
