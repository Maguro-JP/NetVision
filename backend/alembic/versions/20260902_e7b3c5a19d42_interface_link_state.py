"""ポートに、実機の線の状態を持たせる。

Config には「止めていない」までしか書いていない。線がつながっているかは
show の表でしか分からない。図に線があるのに片側が notconnect なら、
それは図と実機のずれそのものなので、持っておいて突き合わせる。

Revision ID: e7b3c5a19d42
Revises: d2a6f9b41c85
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e7b3c5a19d42"
down_revision = "d2a6f9b41c85"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        batch.add_column(sa.Column("link_state", sa.String(length=20), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        batch.drop_column("link_state")
