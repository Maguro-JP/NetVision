from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ConfigSource(enum.StrEnum):
    UPLOAD = "upload"  # 手動アップロード
    COLLECT = "collect"  # 自動収集（将来）


class ConfigSnapshot(Base):
    """取り込んだ Config / show 出力の1世代分。

    原文をそのまま残すのは、パーサを直したときに再解析できるようにするため。
    同じ内容を再取り込みしても世代は増やさない（sha256 で判定）。
    """

    __tablename__ = "config_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), default=None, index=True
    )
    # 台帳に無いホスト名でも取り込めるようにする（後から機器を作って紐付けられる）
    hostname: Mapped[str | None] = mapped_column(String(120), index=True, default=None)

    filename: Mapped[str] = mapped_column(String(255))
    content: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[ConfigSource] = mapped_column(
        Enum(ConfigSource, native_enum=False, length=16), default=ConfigSource.UPLOAD
    )

    # パース結果（台帳へ反映する前の「観測値」）
    vendor: Mapped[str | None] = mapped_column(String(32), default=None)
    parsed_os_name: Mapped[str | None] = mapped_column(String(80), default=None)
    parsed_os_version: Mapped[str | None] = mapped_column(String(80), default=None)
    parsed_model: Mapped[str | None] = mapped_column(String(120), default=None)
    parsed_serial: Mapped[str | None] = mapped_column(String(120), default=None)
    interface_count: Mapped[int] = mapped_column(Integer, default=0)
    neighbor_count: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    device: Mapped[Device | None] = relationship()  # noqa: F821


class Interface(Base):
    """Config から抽出したインターフェース。台帳の一部ではなく観測値として持つ。"""

    __tablename__ = "interfaces"
    __table_args__ = (UniqueConstraint("device_id", "normalized_name", name="uq_interface_device"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)

    name: Mapped[str] = mapped_column(String(120))
    normalized_name: Mapped[str] = mapped_column(String(120), index=True)
    description: Mapped[str | None] = mapped_column(String(255), default=None)
    ip_address: Mapped[str | None] = mapped_column(String(64), default=None)
    # アクセス VLAN。トランクの場合はここは空で、native_vlan / trunk_vlans を見る
    vlan: Mapped[int | None] = mapped_column(Integer, default=None)
    mode: Mapped[str | None] = mapped_column(String(20), default=None)
    native_vlan: Mapped[int | None] = mapped_column(Integer, default=None)
    # 許可 VLAN を `10,20,30-40` の形で持つ。1ポートに数十載ることがあるので、
    # 行に展開せず畳んで入れる。`all`（全許可）はその語をそのまま入れる
    trunk_vlans: Mapped[str | None] = mapped_column(String(512), default=None)
    vrf: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class LinkOrigin(enum.StrEnum):
    DISCOVERED = "discovered"  # LLDP/CDP から自動推定
    MANUAL = "manual"  # 人が図に引いた線


class Link(Base):
    """機器間の結線。

    自動推定した線と、人が引いた線を同じ表で扱う。origin で区別し、
    自動再推定のときに手で引いた線を消さないようにしている。
    """

    __tablename__ = "links"
    __table_args__ = (
        UniqueConstraint(
            "a_device_id", "a_interface", "b_device_id", "b_interface", name="uq_link_endpoints"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    a_device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    a_interface: Mapped[str] = mapped_column(String(120), default="")
    b_device_id: Mapped[int] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), index=True
    )
    b_interface: Mapped[str] = mapped_column(String(120), default="")

    origin: Mapped[LinkOrigin] = mapped_column(
        Enum(LinkOrigin, native_enum=False, length=16), default=LinkOrigin.DISCOVERED, index=True
    )
    protocol: Mapped[str | None] = mapped_column(String(16), default=None)  # cdp / lldp
    label: Mapped[str | None] = mapped_column(String(120), default=None)
    # 双方向で確認できた結線は信頼度が高い
    confirmed_both_ways: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TopologyNode(Base):
    """図の中でのノード配置。人が動かした位置を覚えておく。"""

    __tablename__ = "topology_nodes"
    __table_args__ = (UniqueConstraint("device_id", "layer", name="uq_topology_node"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    device_id: Mapped[int] = mapped_column(ForeignKey("devices.id", ondelete="CASCADE"), index=True)
    # レイヤ別に別々の配置を持てるようにしておく（物理 / L2 / L3）
    layer: Mapped[str] = mapped_column(String(20), default="physical", index=True)

    x: Mapped[float] = mapped_column(Float, default=0.0)
    y: Mapped[float] = mapped_column(Float, default=0.0)
    pinned: Mapped[bool] = mapped_column(Boolean, default=True)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
