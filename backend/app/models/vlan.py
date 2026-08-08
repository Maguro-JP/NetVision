from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class DeviceVlan(Base):
    """機器に定義されている VLAN。

    Config の `vlan 10 / name USERS` から取り込む。VLAN 名まで持つのは、
    図や一覧で「10」とだけ出ても何のセグメントか分からないため。

    どのポートがどの VLAN かは interfaces 側（`Interface.vlan`）が持つので、
    ここは「その機器に定義されている VLAN の一覧」に徹する。
    """

    __tablename__ = "device_vlans"
    __table_args__ = (UniqueConstraint("device_id", "vlan_id", name="uq_device_vlan"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    vlan_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str | None] = mapped_column(String(120), default=None)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
