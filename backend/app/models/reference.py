from __future__ import annotations

import enum
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class ReferenceSource(enum.StrEnum):
    """リファレンスの出どころ。「この日付はどこから来たのか」を必ず残す。"""

    MANUAL = "manual"  # 画面での手入力
    CSV = "csv"  # CSV 取り込み（ベンダー資料からの転記）
    NVD = "nvd"  # NIST NVD
    KEV = "kev"  # CISA KEV（悪用が確認された脆弱性）
    VENDOR = "vendor"  # ベンダーの EoX / PSIRT データ


class ProductReference(Base):
    """型番ごとのライフサイクル情報。

    EoL は本来「型番」の属性であり、機器ごとに持つと同じ日付を何度も入力することになり、
    ベンダーが訂正したときに全台直す羽目になる。ここに正規化して1か所で管理する。

    機器側に日付が入っている場合はそちらを優先する（個体の事情を上書きできるようにする）。
    """

    __tablename__ = "product_references"
    __table_args__ = (UniqueConstraint("vendor", "model", name="uq_product_reference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    vendor: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str] = mapped_column(String(120), index=True)

    product_name: Mapped[str | None] = mapped_column(String(200), default=None)
    eos_date: Mapped[date | None] = mapped_column(Date, default=None)  # 販売終了
    eol_date: Mapped[date | None] = mapped_column(Date, default=None)  # サポート終了
    end_of_sw_date: Mapped[date | None] = mapped_column(Date, default=None)  # ソフト保守終了
    last_support_date: Mapped[date | None] = mapped_column(Date, default=None)  # 最終サポート日
    recommended_replacement: Mapped[str | None] = mapped_column(String(120), default=None)

    # 出典と鮮度。閉域運用では「いつ時点の情報か」が特に重要になる。
    source: Mapped[ReferenceSource] = mapped_column(
        Enum(ReferenceSource, native_enum=False, length=16),
        default=ReferenceSource.MANUAL,
        index=True,
    )
    source_url: Mapped[str | None] = mapped_column(String(500), default=None)
    fetched_at: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    notes: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Vulnerability(Base):
    """脆弱性1件。NVD / CISA KEV / ベンダー PSIRT から取り込む。"""

    __tablename__ = "vulnerabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    cve_id: Mapped[str] = mapped_column(String(30), unique=True, index=True)

    title: Mapped[str | None] = mapped_column(String(300), default=None)
    description: Mapped[str | None] = mapped_column(Text, default=None)
    cvss_score: Mapped[float | None] = mapped_column(Float, default=None, index=True)
    cvss_severity: Mapped[str | None] = mapped_column(String(16), default=None, index=True)
    published: Mapped[date | None] = mapped_column(Date, default=None, index=True)
    last_modified: Mapped[date | None] = mapped_column(Date, default=None)

    # CISA KEV に載っている＝実際に悪用が確認されている。優先度判断で最も効く情報。
    known_exploited: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    remediation_due: Mapped[date | None] = mapped_column(Date, default=None)

    source: Mapped[ReferenceSource] = mapped_column(
        Enum(ReferenceSource, native_enum=False, length=16),
        default=ReferenceSource.MANUAL,
        index=True,
    )
    source_url: Mapped[str | None] = mapped_column(String(500), default=None)
    fetched_at: Mapped[date | None] = mapped_column(Date, default=None, index=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    affects: Mapped[list[VulnerabilityAffect]] = relationship(
        back_populates="vulnerability", cascade="all, delete-orphan"
    )


class VulnerabilityAffect(Base):
    """脆弱性の影響範囲。「どのベンダーの、どの型番の、どのバージョンまで」を表す。

    型番が空なら「そのベンダーのその製品系列すべて」の意味になる。
    バージョン範囲は NVD の表現（including / excluding）に合わせている。
    """

    __tablename__ = "vulnerability_affects"

    id: Mapped[int] = mapped_column(primary_key=True)
    vulnerability_id: Mapped[int] = mapped_column(
        ForeignKey("vulnerabilities.id", ondelete="CASCADE"), index=True
    )

    vendor: Mapped[str] = mapped_column(String(80), index=True)
    model: Mapped[str | None] = mapped_column(String(120), default=None, index=True)
    # OS名での絞り込み（IOS-XE のみ、など）
    os_name: Mapped[str | None] = mapped_column(String(80), default=None)

    version_start_including: Mapped[str | None] = mapped_column(String(80), default=None)
    version_start_excluding: Mapped[str | None] = mapped_column(String(80), default=None)
    version_end_including: Mapped[str | None] = mapped_column(String(80), default=None)
    version_end_excluding: Mapped[str | None] = mapped_column(String(80), default=None)
    exact_version: Mapped[str | None] = mapped_column(String(80), default=None)

    fixed_version: Mapped[str | None] = mapped_column(String(80), default=None)

    vulnerability: Mapped[Vulnerability] = relationship(back_populates="affects")
