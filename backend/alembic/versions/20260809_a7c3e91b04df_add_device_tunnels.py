"""トンネル（論理構成図の材料）を持たせる

Revision ID: a7c3e91b04df
Revises: d5e2b71fa93c
Create Date: 2026-08-09

物理では何段も経由していても、論理ではセンターと直結して見えることがある。
そのズレを図に出すために、Config から読んだトンネルを機器ごとに保存する。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a7c3e91b04df"
down_revision = "d5e2b71fa93c"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "device_tunnels",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=True),
        sa.Column("source", sa.String(length=120), nullable=True),
        sa.Column("destination", sa.String(length=120), nullable=True),
        sa.Column("vrf", sa.String(length=120), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        # SQLite には now() が無い。CURRENT_TIMESTAMP なら両方で通る
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.UniqueConstraint("device_id", "name", name="uq_device_tunnel"),
    )
    op.create_index("ix_device_tunnels_device_id", "device_tunnels", ["device_id"])
    op.create_index("ix_device_tunnels_destination", "device_tunnels", ["destination"])


def downgrade() -> None:
    op.drop_index("ix_device_tunnels_destination", table_name="device_tunnels")
    op.drop_index("ix_device_tunnels_device_id", table_name="device_tunnels")
    op.drop_table("device_tunnels")
