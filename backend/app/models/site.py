from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class Site(Base):
    """機器の設置拠点（データセンター / 事業所 / フロアなど）。"""

    __tablename__ = "sites"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: 人が決める拠点ID。プロジェクト既定の番号をそのまま使える。空は未設定
    code: Mapped[str | None] = mapped_column(String(80), unique=True, default=None, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    address: Mapped[str | None] = mapped_column(String(255), default=None)
    notes: Mapped[str | None] = mapped_column(String(1000), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    devices: Mapped[list[Device]] = relationship(  # noqa: F821
        back_populates="site", cascade="save-update"
    )
