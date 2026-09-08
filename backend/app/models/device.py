from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, and_, event, func
from sqlalchemy.orm import Mapped, foreign, mapped_column, relationship

from app.db import Base
from app.models.reference import ProductReference


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
    #: 人が決める管理ID。社内台帳の番号など。空は未設定
    code: Mapped[str | None] = mapped_column(String(80), unique=True, default=None, index=True)
    #: 人が付ける機器の名称。「本社コア1号機」など。空ならホスト名で呼ぶ。
    #: ホスト名は機械に付けた名前で、現場の呼び名と食い違うことがある
    label: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    # ベンダーと型番は Config を読ませれば埋まる。台帳を起こす時点では空でよい。
    # NULL ではなく空文字で持つ。二種類の「無い」があると、絞り込みの条件が
    # 場所ごとに食い違う（未入力は missing_fields で拾う）
    vendor: Mapped[str] = mapped_column(String(80), index=True, default="", server_default="")
    model: Mapped[str] = mapped_column(String(120), index=True, default="", server_default="")
    #: 型番の突き合わせ用の鍵（versions.model_key）。保存時に vendor / model から作る
    model_key: Mapped[str] = mapped_column(String(120), index=True, default="", server_default="")
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

    #: 揃っていないと道具として困る項目。名前は画面にそのまま出す。
    #: 型番とメーカーは EoL と脆弱性を引く鍵、拠点は図と一覧の絞り込みの軸。
    REQUIRED_FIELDS: tuple[tuple[str, str], ...] = (
        ("vendor", "メーカー"),
        ("model", "型番"),
        ("site_id", "拠点"),
    )

    @property
    def missing_fields(self) -> list[str]:
        """埋まっていない項目の名前。

        Config から機器を起こすと、読み取れなかった項目は空のまま載る。
        空で載せること自体は正しい（推測で埋めると後から直せない）が、
        空のまま埋もれると、EoL の判定漏れに後で気づくことになる。
        一覧で目に付くよう、何が足りないかを機器自身に答えさせる。
        """
        return [label for name, label in self.REQUIRED_FIELDS if not getattr(self, name, None)]

    # 型番のライフサイクル情報。EoL は本来型番の属性なので製品マスターに正規化してある。
    # ベンダーは大文字小文字を無視し、型番は飾りを外した鍵（model_key）で突き合わせる
    # （`WS-C2960X-48FPD-L` の機器に `C2960X-48FPD-L` の EoL が当たるように）。
    product: Mapped[ProductReference | None] = relationship(
        primaryjoin=lambda: and_(
            func.lower(foreign(Device.vendor)) == func.lower(ProductReference.vendor),
            foreign(Device.model_key) == ProductReference.model_key,
            ProductReference.model_key != "",
        ),
        viewonly=True,
        uselist=False,
        lazy="selectin",
    )
    contracts: Mapped[list[MaintenanceContract]] = relationship(  # noqa: F821
        back_populates="device",
        cascade="all, delete-orphan",
        order_by="MaintenanceContract.end_date",
    )


@event.listens_for(Device, "before_insert")
@event.listens_for(Device, "before_update")
def _fill_device_model_key(_mapper, _connection, device: Device) -> None:
    from app.services.versions import model_key

    device.model_key = model_key(device.vendor, device.model)
