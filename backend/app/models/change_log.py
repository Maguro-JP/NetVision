from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import DateTime, Enum, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class ChangeAction(enum.StrEnum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"


class ChangeSource(enum.StrEnum):
    """変更がどこから発生したか。台帳取り込みと手入力を区別できるようにする。"""

    UI = "ui"  # 画面 / API からの個別操作
    IMPORT = "import"  # 台帳の取り込み
    SEED = "seed"  # デモデータ投入
    SYSTEM = "system"  # 移行処理など


class ChangeLog(Base):
    """構成変更の履歴。1レコード = 1項目の変更。

    「いつ・どこで・何が・どう変わったか」を残すのが目的なので、
    対象が削除された後も読めるよう、対象の表示名（entity_label）は文字列で持つ。
    """

    __tablename__ = "change_logs"

    id: Mapped[int] = mapped_column(primary_key=True)

    entity_type: Mapped[str] = mapped_column(String(40), index=True)  # device / contract / site
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True, default=None)
    entity_label: Mapped[str] = mapped_column(String(200), index=True)

    action: Mapped[ChangeAction] = mapped_column(
        Enum(ChangeAction, native_enum=False, length=16), index=True
    )
    field: Mapped[str | None] = mapped_column(String(60), default=None)
    old_value: Mapped[str | None] = mapped_column(Text, default=None)
    new_value: Mapped[str | None] = mapped_column(Text, default=None)

    source: Mapped[ChangeSource] = mapped_column(
        Enum(ChangeSource, native_enum=False, length=16), default=ChangeSource.UI, index=True
    )
    actor: Mapped[str | None] = mapped_column(String(120), default=None)
    # 取り込み1回分など、まとめて発生した変更を束ねるID
    batch_id: Mapped[str | None] = mapped_column(String(40), index=True, default=None)
    note: Mapped[str | None] = mapped_column(String(255), default=None)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
