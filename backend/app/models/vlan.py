from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Vlan(Base):
    """VLAN 台帳。番号ごとに1行の、人が管理する側の情報。

    実機の Config に書いてある名前は 32 文字までだったり、機器ごとに表記が
    揺れていたりする（同じセグメントが `SRV` と `SERVERS` になっている等）。
    数十〜数百の VLAN を扱う現場では、どれが何のためのセグメントかを
    人が書ける場所が要るので、実機の観測値（DeviceVlan）とは分けて持つ。

    ここに書いた名前は Config を取り込み直しても上書きされない。
    """

    __tablename__ = "vlans"

    id: Mapped[int] = mapped_column(primary_key=True)
    vlan_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)

    name: Mapped[str | None] = mapped_column(String(120), default=None)
    purpose: Mapped[str | None] = mapped_column(String(255), default=None)  # 用途
    owner: Mapped[str | None] = mapped_column(String(120), default=None)  # 管理部署・担当
    subnet: Mapped[str | None] = mapped_column(String(64), default=None)  # 10.20.30.0/24
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DeviceVlan(Base):
    """機器に定義されている VLAN。実機の観測値で、人は編集しない。

    Config の `vlan 10 / name USERS` から取り込む。名前を持つのは、台帳側に
    まだ登録が無い VLAN でも番号だけよりは分かるようにするため。また、
    台帳の名前と食い違っていれば「機器側の設定が古い」ことに気づける。

    どのポートがどの VLAN かは interfaces 側が持つ。ここは
    「その機器に定義されている VLAN の一覧」に徹する。
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


class DeviceVrf(Base):
    """機器の中に切った仮想ルーター。

    ベンダーによって呼び方が違うが（Cisco/NEC IX = VRF、Comware = VPN インスタンス、
    Junos = routing-instance、FortiGate = VDOM）、
    1台の中で経路表を分けるという点は同じなので1つの表で扱う。

    図の上では同じ線で繋がって見えても、VRF が違えば通信できない。
    「繋がっているのに通らない」の原因になりやすいので、明示的に持って
    絞り込めるようにしてある。
    """

    __tablename__ = "device_vrfs"
    __table_args__ = (UniqueConstraint("device_id", "name", name="uq_device_vrf"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120), index=True)
    rd: Mapped[str | None] = mapped_column(String(64), default=None)  # route-distinguisher
    description: Mapped[str | None] = mapped_column(String(255), default=None)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
