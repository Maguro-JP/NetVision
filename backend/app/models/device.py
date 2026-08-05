from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DeviceType(enum.StrEnum):
    ROUTER = "router"
    SWITCH = "switch"
    FIREWALL = "firewall"
    LOAD_BALANCER = "load_balancer"
    WIRELESS_AP = "wireless_ap"
    WIRELESS_CONTROLLER = "wireless_controller"
    SERVER = "server"
    OTHER = "other"


class DeviceStatus(enum.StrEnum):
    ACTIVE = "active"  # 稼働中
    STANDBY = "standby"  # 予備機 / コールドスタンバイ
    MAINTENANCE = "maintenance"  # 保守作業中
    RETIRED = "retired"  # 撤去済み


class Device(Base):
    """ネットワーク機器1台。型番・バージョン・導入時期・ライフサイクル日付を保持する。"""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(primary_key=True)

    # 識別情報
    hostname: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    vendor: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)
    serial_number: Mapped[str | None] = mapped_column(String(120), unique=True, default=None)
    device_type: Mapped[DeviceType] = mapped_column(
        Enum(DeviceType, native_enum=False, length=32), default=DeviceType.OTHER, index=True
    )
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, native_enum=False, length=32), default=DeviceStatus.ACTIVE, index=True
    )

    # バージョン
    os_name: Mapped[str | None] = mapped_column(String(80), default=None)
    os_version: Mapped[str | None] = mapped_column(String(80), default=None, index=True)
    firmware_version: Mapped[str | None] = mapped_column(String(80), default=None)

    # 設置場所
    site_id: Mapped[int | None] = mapped_column(
        ForeignKey("sites.id", ondelete="SET NULL"), default=None, index=True
    )
    rack: Mapped[str | None] = mapped_column(String(60), default=None)
    rack_unit: Mapped[str | None] = mapped_column(String(30), default=None)
    mgmt_ip: Mapped[str | None] = mapped_column(String(45), default=None)

    # ライフサイクル
    install_date: Mapped[date | None] = mapped_column(Date, default=None)
    eos_date: Mapped[date | None] = mapped_column(Date, default=None)  # 販売終了 End of Sale
    eol_date: Mapped[date | None] = mapped_column(Date, default=None)  # サポート終了 End of Support

    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    site: Mapped[Site | None] = relationship(back_populates="devices")  # noqa: F821
    contracts: Mapped[list[MaintenanceContract]] = relationship(  # noqa: F821
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="MaintenanceContract.end_date",
    )
