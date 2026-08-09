from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeviceTunnel(Base):
    """機器に設定されているトンネル（GRE / IPsec など）。

    論理構成図の材料。物理では「拠点 → 支社 → センター」と何段も経由していても、
    論理ではセンターと直結して見えることがある。**そのズレが障害の切り分けで効く**
    ので、物理の結線とは別に持つ。

    宛先は Config に書かれている値（多くは IP）をそのまま入れる。相手機器の
    特定は台帳の IP と突き合わせて図を作るときに行い、**当たらなければ
    当たらないままにする**。それらしい相手を当てはめると、実際には繋がって
    いない線が図に出てしまう。
    """

    __tablename__ = "device_tunnels"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_device_tunnel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str | None] = mapped_column(String(40), default=None)  # gre / ipsec など
    source: Mapped[str | None] = mapped_column(String(120), default=None)
    destination: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    vrf: Mapped[str | None] = mapped_column(String(120), default=None)
    description: Mapped[str | None] = mapped_column(String(255), default=None)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
