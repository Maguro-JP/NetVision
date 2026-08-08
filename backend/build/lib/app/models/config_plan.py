from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class PlanStatus(enum.StrEnum):
    DRAFT = "draft"  # 作成中
    REVIEW = "review"  # レビュー中
    APPROVED = "approved"  # 投入待ち
    APPLIED = "applied"  # 投入済み
    ARCHIVED = "archived"  # 見送り


class ConfigPlan(Base):
    """ある機器の「将来こうしたい」config。

    使い道が3つあり、それぞれ出したいものが違う。

    - 現行フル … 取り込んだ running-config そのもの。機器故障時の復旧に使う
    - 将来フル … この plan の body。更改で入る新機種にそのまま投入する
    - 差分   … 現行を将来に合わせる投入コマンド。更改に合わせて既存機器に当てる

    現行フルは取り込み済みの config スナップショットを指すだけで、ここには持たない。
    「どのスナップショットを基準に作ったか」を記録しておき、後から現行が更新された
    ときに「基準が古い」と気付けるようにする。
    """

    __tablename__ = "config_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, native_enum=False, length=16), default=PlanStatus.DRAFT, index=True
    )

    # 差分の基準にした現行スナップショット。現行が入れ替わったら差分を作り直す必要がある
    base_snapshot_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_snapshots.id", ondelete="SET NULL"), default=None, index=True
    )
    # 更改シナリオとの紐付け（フェーズ1.7 のシナリオ）
    scenario_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenarios.id", ondelete="SET NULL"), default=None, index=True
    )

    # 将来フル。ここが本体
    body: Mapped[str] = mapped_column(Text, default="")

    # 差分の解釈に効くので、どのベンダーの書式として扱うかを持っておく。
    # 未指定なら機器のベンダーから解決する
    vendor: Mapped[str | None] = mapped_column(String(32), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)

    device: Mapped[Device] = relationship()  # noqa: F821
