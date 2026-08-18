"""VLAN 台帳・トランク情報・VRF を追加する

Revision ID: c4d1a8f26b57
Revises: b2c9e14d7f30
Create Date: 2026-08-09

VLAN を3つの顔（アクセス / ネイティブ / トランク許可）で持てるようにし、
人が用途を書く VLAN 台帳と、機器の中の仮想ルーター（VRF）を足す。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c4d1a8f26b57"
down_revision = "b2c9e14d7f30"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "vlans",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("vlan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("purpose", sa.String(length=255), nullable=True),
        sa.Column("owner", sa.String(length=120), nullable=True),
        sa.Column("subnet", sa.String(length=64), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
    )
    op.create_index("ix_vlans_vlan_id", "vlans", ["vlan_id"], unique=True)

    op.create_table(
        "device_vrfs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "device_id",
            sa.Integer(),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("rd", sa.String(length=64), nullable=True),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")
        ),
        sa.UniqueConstraint("device_id", "name", name="uq_device_vrf"),
    )
    op.create_index("ix_device_vrfs_device_id", "device_vrfs", ["device_id"])
    op.create_index("ix_device_vrfs_name", "device_vrfs", ["name"])

    with op.batch_alter_table("interfaces") as batch:
        batch.add_column(sa.Column("native_vlan", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("trunk_vlans", sa.String(length=512), nullable=True))
        batch.add_column(sa.Column("vrf", sa.String(length=120), nullable=True))
    op.create_index("ix_interfaces_vrf", "interfaces", ["vrf"])


def downgrade() -> None:
    op.drop_index("ix_interfaces_vrf", table_name="interfaces")
    with op.batch_alter_table("interfaces") as batch:
        batch.drop_column("vrf")
        batch.drop_column("trunk_vlans")
        batch.drop_column("native_vlan")

    op.drop_index("ix_device_vrfs_name", table_name="device_vrfs")
    op.drop_index("ix_device_vrfs_device_id", table_name="device_vrfs")
    op.drop_table("device_vrfs")

    op.drop_index("ix_vlans_vlan_id", table_name="vlans")
    op.drop_table("vlans")
