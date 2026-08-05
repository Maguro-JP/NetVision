from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class MaintenanceContract(Base):
    """機器に紐づく保守契約。更改・予算計画の判断材料になる。"""

    __tablename__ = "maintenance_contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)

    contract_number: Mapped[str | None] = mapped_column(String(120), default=None)
    provider: Mapped[str] = mapped_column(String(120))  # 保守ベンダー
    service_level: Mapped[str | None] = mapped_column(
        String(120), default=None
    )  # 例: 24x365 4h駆付
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    annual_cost: Mapped[float | None] = mapped_column(Numeric(12, 2), default=None)
    contact: Mapped[str | None] = mapped_column(String(255), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    device: Mapped[Device] = relationship(back_populates="contracts")  # noqa: F821
