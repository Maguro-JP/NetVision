from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Enum, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class RemediationKind(enum.StrEnum):
    UPGRADE = "upgrade"  # 修正版に上げる
    WORKAROUND = "workaround"  # 設定で回避する
    MITIGATION = "mitigation"  # 影響を下げる（完全には塞がらない）
    ACCEPTED = "accepted"  # リスクを受容する（理由を残す）
    NOT_APPLICABLE = "not_applicable"  # 構成上そもそも影響しない


class Remediation(Base):
    """脆弱性への対処。

    公開データ（NVD / KEV）には「このコマンドで回避できる」という機械可読な
    情報が入っていない。ベンダーのアドバイザリに文章で書いてあるだけなので、
    一度調べた対処を人が書いて資産として残すのがこのモデルの役目。

    対処はベンダー・型番・バージョンで変わるので、資料やテンプレートと同じ
    3軸を持つ。こうしておくと、同じ型番・同じバージョン帯の機器すべてで
    一度書いた対処が再利用される。
    """

    __tablename__ = "remediations"

    id: Mapped[int] = mapped_column(primary_key=True)
    vulnerability_id: Mapped[int] = mapped_column(
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"), index=True
    )

    kind: Mapped[RemediationKind] = mapped_column(
        Enum(RemediationKind, native_enum=False, length=20),
        default=RemediationKind.WORKAROUND,
        index=True,
    )

    # 適用対象の3軸。資料・テンプレートと同じ考え方
    vendor: Mapped[str | None] = mapped_column(String(80), default=None, index=True)
    model: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    os_name: Mapped[str | None] = mapped_column(String(80), default=None)
    applies_from: Mapped[str | None] = mapped_column(String(80), default=None)
    applies_to: Mapped[str | None] = mapped_column(String(80), default=None)

    summary: Mapped[str] = mapped_column(String(300))
    fixed_version: Mapped[str | None] = mapped_column(String(80), default=None)
    # 回避策の手順。コマンドを含む文章でよい
    procedure: Mapped[str | None] = mapped_column(Text, default=None)

    # 根拠にした資料と、対処に使えるテンプレート
    source_document_id: Mapped[int | None] = mapped_column(
        ForeignKey("documents.id", ondelete="SET NULL"), default=None, index=True
    )
    config_template_id: Mapped[int | None] = mapped_column(
        ForeignKey("config_templates.id", ondelete="SET NULL"), default=None, index=True
    )

    # 誰がいつ確認したか。古い対処を鵜呑みにしないため
    verified_by: Mapped[str | None] = mapped_column(String(120), default=None)
    verified_on: Mapped[date | None] = mapped_column(Date, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    vulnerability: Mapped[Vulnerability] = relationship()  # noqa: F821
    source_document: Mapped[Document | None] = relationship()  # noqa: F821
    config_template: Mapped[ConfigTemplate | None] = relationship()  # noqa: F821
