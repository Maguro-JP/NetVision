"""ポートの MTU・速度・二重・束ね方を持たせる。

線の両端でこれらが食い違うと、通信は「だいたい動くのに、たまに固まる」
という形で残る。切り分けにいちばん時間がかかる壊れ方なので、読み取って
持っておき、両端で突き合わせられるようにする。

Revision ID: c1f5e8a37d20
Revises: b8d4a1f37c62
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c1f5e8a37d20"
down_revision = "b8d4a1f37c62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        batch.add_column(sa.Column("mtu", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("speed", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("duplex", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("lag_group", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("lag_mode", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        for name in ("lag_mode", "lag_group", "duplex", "speed", "mtu"):
            batch.drop_column(name)
