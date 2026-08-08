"""add config plans

Revision ID: a17c3b90d4e2
Revises: c504e129fb92
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "a17c3b90d4e2"
down_revision: str | None = "c504e129fb92"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "config_plans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "review",
                "approved",
                "applied",
                "archived",
                name="planstatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("base_snapshot_id", sa.Integer(), nullable=True),
        sa.Column("scenario_id", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("vendor", sa.String(length=32), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["base_snapshot_id"], ["config_snapshots.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scenario_id"], ["scenarios.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_config_plans_device_id", "config_plans", ["device_id"])
    op.create_index("ix_config_plans_status", "config_plans", ["status"])
    op.create_index("ix_config_plans_base_snapshot_id", "config_plans", ["base_snapshot_id"])
    op.create_index("ix_config_plans_scenario_id", "config_plans", ["scenario_id"])


def downgrade() -> None:
    op.drop_index("ix_config_plans_scenario_id", table_name="config_plans")
    op.drop_index("ix_config_plans_base_snapshot_id", table_name="config_plans")
    op.drop_index("ix_config_plans_status", table_name="config_plans")
    op.drop_index("ix_config_plans_device_id", table_name="config_plans")
    op.drop_table("config_plans")
