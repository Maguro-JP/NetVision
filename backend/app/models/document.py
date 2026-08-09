from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class DocumentType(enum.StrEnum):
    COMMAND_REFERENCE = "command_reference"  # コマンドリファレンス
    CONFIG_GUIDE = "config_guide"  # コンフィグレーションガイド
    RELEASE_NOTES = "release_notes"  # リリースノート
    INSTALL_GUIDE = "install_guide"  # 設置・導入ガイド
    HARDWARE_GUIDE = "hardware_guide"  # ハードウェアガイド
    SECURITY_ADVISORY = "security_advisory"  # セキュリティアドバイザリ
    DATASHEET = "datasheet"  # データシート
    OTHER = "other"


class Document(Base):
    """ベンダーのマニュアル・コマンドリファレンス。

    版が変わるたびに新しい行を積み上げ、古い版は消さない。
    現場では古いバージョンが現役で動いていることが普通にあるため、
    「新しいのが出たから消す」をしてしまうと、その機器の手引きが失われる。

    新旧は superseded_by で繋ぐだけで、どちらも残る。
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)

    vendor: Mapped[str] = mapped_column(String(80), index=True)
    # 型番。空なら「そのベンダーの該当シリーズ全体」。`C9500*` の前方一致も書ける。
    model: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    os_name: Mapped[str | None] = mapped_column(String(80), default=None)

    doc_type: Mapped[DocumentType] = mapped_column(
        Enum(DocumentType, native_enum=False, length=24),
        default=DocumentType.OTHER,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(300))
    language: Mapped[str | None] = mapped_column(String(16), default=None)

    # 適用バージョンは「点」ではなく「範囲」で持つ。マニュアルは 17.9.x 向けのように
    # 系列単位で出るため、範囲で持たないと機器のバージョンから引けない。
    # 両方 None なら全バージョン共通の資料とみなす。
    applies_from: Mapped[str | None] = mapped_column(String(80), default=None)
    applies_to: Mapped[str | None] = mapped_column(String(80), default=None)
    version_label: Mapped[str | None] = mapped_column(String(80), default=None)  # 表示用「17.9.x」

    edition: Mapped[str | None] = mapped_column(
        String(80), default=None
    )  # 資料自体の版（Rev.C など）
    published_at: Mapped[date | None] = mapped_column(Date, default=None)

    # 実体はファイル領域に置き、DB にはパスとハッシュだけ持つ
    storage_path: Mapped[str | None] = mapped_column(String(400), default=None)
    filename: Mapped[str | None] = mapped_column(String(255), default=None)
    content_type: Mapped[str | None] = mapped_column(String(120), default=None)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    sha256: Mapped[str | None] = mapped_column(String(64), default=None, index=True)

    source_url: Mapped[str | None] = mapped_column(String(700), default=None)
    fetched_at: Mapped[date | None] = mapped_column(Date, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    # 後継資料。古い版を消さずに「新しいのがある」ことだけ示す。
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None, index=True
    )
    superseded_by: Mapped[Document | None] = relationship(remote_side="Document.id")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class TemplateCategory(enum.StrEnum):
    INTERFACE = "interface"
    VLAN = "vlan"
    ROUTING = "routing"
    ACL = "acl"
    QOS = "qos"
    MANAGEMENT = "management"  # SNMP / NTP / Syslog / AAA
    SECURITY = "security"
    OTHER = "other"


class ConfigTemplate(Base):
    """設定変更用のコマンドテンプレート。

    コマンドの書式はメーカー・型番・バージョンで変わるので、
    ドキュメントと同じ3軸（ベンダー / 型番 / バージョン範囲）で管理する。
    こうしておくと、機器を選ぶだけでその機器に合う書式のテンプレートが選ばれる。

    根拠にしたマニュアルへリンクできるようにしてある（source_document）。
    「この書式どこ見て書いたの？」に答えられないテンプレートは保守できない。
    """

    __tablename__ = "config_templates"

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(200), index=True)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    category: Mapped[TemplateCategory] = mapped_column(
        Enum(TemplateCategory, native_enum=False, length=20),
        default=TemplateCategory.OTHER,
        index=True,
    )

    vendor: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    os_name: Mapped[str | None] = mapped_column(String(80), default=None)
    applies_from: Mapped[str | None] = mapped_column(String(80), default=None)
    applies_to: Mapped[str | None] = mapped_column(String(80), default=None)
    version_label: Mapped[str | None] = mapped_column(String(80), default=None)

    body: Mapped[str] = mapped_column(Text)
    # [{"name": "vlan_id", "label": "VLAN ID", "required": true, "default": "", "help": ""}]
    variables: Mapped[list] = mapped_column(JSON, default=list)

    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None, index=True
    )
    source_document: Mapped[Document | None] = relationship()

    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_templates.id", ondelete="SET NULL"), default=None, index=True
    )
    superseded_by: Mapped[ConfigTemplate | None] = relationship(remote_side="ConfigTemplate.id")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
