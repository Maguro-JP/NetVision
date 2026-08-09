"""結線の引き出し位置を持たせる

Revision ID: d5e2b71fa93c
Revises: c4d1a8f26b57
Create Date: 2026-08-09

図の上で線を機器のどの辺から出すかを、人が直したときだけ覚える。
空のままなら位置関係から自動で決める。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d5e2b71fa93c"
down_revision = "c4d1a8f26b57"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("links") as batch:
        batch.add_column(sa.Column("a_side", sa.String(length=8), nullable=True))
        batch.add_column(sa.Column("b_side", sa.String(length=8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("links") as batch:
        batch.drop_column("b_side")
        batch.drop_column("a_side")
