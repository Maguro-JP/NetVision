"""図の枠グループと、キャリア網の段。

Revision ID: b7e4d12c90aa
Revises: a1b2c3d4e5f6
Create Date: 2026-08-14

大規模な網では、コア／メトロ／アクセスを枠で分けて読む。
人が選んだ機器を枠で囲み、色を残す。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b7e4d12c90aa"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "topology_groups",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("layer", sa.String(length=20), nullable=False, server_default="physical"),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("plane", sa.String(length=20), nullable=False, server_default="custom"),
        sa.Column("color", sa.String(length=16), nullable=False, server_default="#5a7a9a"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_topology_groups_layer", "topology_groups", ["layer"])
    op.create_table(
        "topology_group_members",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("group_id", sa.Integer(), sa.ForeignKey("topology_groups.id", ondelete="CASCADE")),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE")),
        sa.UniqueConstraint("group_id", "device_id", name="uq_topology_group_member"),
    )
    op.create_index("ix_topology_group_members_group_id", "topology_group_members", ["group_id"])
    op.create_index("ix_topology_group_members_device_id", "topology_group_members", ["device_id"])


def downgrade() -> None:
    op.drop_index("ix_topology_group_members_device_id", table_name="topology_group_members")
    op.drop_index("ix_topology_group_members_group_id", table_name="topology_group_members")
    op.drop_table("topology_group_members")
    op.drop_index("ix_topology_groups_layer", table_name="topology_groups")
    op.drop_table("topology_groups")
