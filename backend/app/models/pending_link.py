from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class PendingLink(Base):
    """まだ機器が揃っていない結線。

    導入のとき、手元にあるのは結線表だけということがある。機器を1台ずつ
    登録し終えるまで結線を入れられないのでは、順番の都合を人に押し付ける
    ことになる。そこで、ホスト名のまま預かっておいて、両端の機器が台帳に
    載った時点で本物の結線に変える。

    預かるだけで、勝手に機器を作ることはしない。結線表に書かれた名前が
    正しいとは限らず、打ち間違いから架空の機器が生まれる方が困るため。
    どの行がまだ繋がっていないかは一覧で見えるようにしてある。
    """

    __tablename__ = "pending_links"
    __table_args__ = (
        UniqueConstraint(
            "a_hostname", "a_interface", "b_hostname", "b_interface", name="uq_pending_link"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    a_hostname: Mapped[str] = mapped_column(String(120), index=True)
    a_interface: Mapped[str] = mapped_column(String(120), default="")
    b_hostname: Mapped[str] = mapped_column(String(120), index=True)
    b_interface: Mapped[str] = mapped_column(String(120), default="")

    label: Mapped[str | None] = mapped_column(String(120), default=None)
    #: 取り込んだファイル名。どの表から来た行か分かるように残す
    source: Mapped[str | None] = mapped_column(String(255), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
