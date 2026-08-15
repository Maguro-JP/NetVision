"""機器に、人が付ける名称を持たせる

Revision ID: d3a7c1e5b982
Revises: c9f1a2b3d4e5
Create Date: 2026-08-15

ホスト名は機械に付けた名前で、現場では別の呼び名を使っていることがある
（「本社コア1号機」など）。図や一覧では呼び名で探したいので、ホスト名とは
別に持つ。空は未設定（NULL）で、そのときはホスト名を使う。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d3a7c1e5b982"
down_revision = "c9f1a2b3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("label", sa.String(length=120), nullable=True))
        batch.create_index("ix_devices_label", ["label"])


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_index("ix_devices_label")
        batch.drop_column("label")
