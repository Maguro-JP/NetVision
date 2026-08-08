"""add device vlans

Revision ID: b2c9e14d7f30
Revises: f93b2d5e81a4
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b2c9e14d7f30"
down_revision: str | None = "f93b2d5e81a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_vlans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("vlan_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "vlan_id", name="uq_device_vlan"),
    )
    op.create_index("ix_device_vlans_device_id", "device_vlans", ["device_id"])
    op.create_index("ix_device_vlans_vlan_id", "device_vlans", ["vlan_id"])


def downgrade() -> None:
    op.drop_table("device_vlans")
