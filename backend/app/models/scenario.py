from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ScenarioStatus(enum.StrEnum):
    DRAFT = "draft"  # 検討中
    REVIEW = "review"  # 稟議中
    APPROVED = "approved"  # 承認済み
    ARCHIVED = "archived"  # 見送り / 完了


class ScenarioAction(enum.StrEnum):
    REPLACE = "replace"  # 既存機器を入れ替える
    ADD = "add"  # 新規に追加する
    REMOVE = "remove"  # 撤去する
    KEEP = "keep"  # そのまま残す（明示的に「据え置き」と決めた機器）


class Scenario(Base):
    """将来構成の検討案。

    実機の台帳（devices）には一切手を触れず、「こう変えたらどうなるか」だけを持つ。
    採用が決まったら台帳に反映する、という運用を想定している。
    """

    __tablename__ = "scenarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[ScenarioStatus] = mapped_column(
        Enum(ScenarioStatus, native_enum=False, length=16),
        default=ScenarioStatus.DRAFT,
        index=True,
    )
    # いつ時点の構成として評価するか（未指定なら今日）
    target_date: Mapped[date | None] = mapped_column(Date, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list[ScenarioItem]] = relationship(
        back_populates="scenario", cascade="all, delete-orphan", order_by="ScenarioItem.id"
    )


class ScenarioItem(Base):
    """シナリオ内の1つの変更。"""

    __tablename__ = "scenario_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey("scenarios.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[ScenarioAction] = mapped_column(
        Enum(ScenarioAction, native_enum=False, length=16), index=True
    )

    # 対象の既存機器（add の場合は無し）。機器が消えても案は残るよう SET NULL。
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="SET NULL"), default=None, index=True
    )
    # 機器が削除された後も何の案だったか分かるように名前を持っておく
    device_label: Mapped[str | None] = mapped_column(String(160), default=None)

    # 導入する機器（replace / add のとき）
    planned_vendor: Mapped[str | None] = mapped_column(String(80), default=None)
    planned_model: Mapped[str | None] = mapped_column(String(120), default=None)
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    unit_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), default=None)
    planned_annual_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), default=None)
    planned_install_date: Mapped[date | None] = mapped_column(Date, default=None)
    planned_eol_date: Mapped[date | None] = mapped_column(Date, default=None)

    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    scenario: Mapped[Scenario] = relationship(back_populates="items")
