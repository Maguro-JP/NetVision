"""add device exposures

Revision ID: f93b2d5e81a4
Revises: e2f8a41c7b93
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f93b2d5e81a4"
down_revision: str | None = "e2f8a41c7b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "device_exposures",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.Integer(), nullable=False),
        sa.Column("vulnerability_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "in_progress",
                "mitigated",
                "fixed",
                "accepted",
                "not_affected",
                "resolved",
                name="exposurestatus",
                native_enum=False,
                length=16,
            ),
            nullable=False,
        ),
        sa.Column("remediation_id", sa.Integer(), nullable=True),
        sa.Column("decided_by", sa.String(length=120), nullable=True),
        sa.Column("decided_on", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("decided_os_version", sa.String(length=80), nullable=True),
        sa.Column("config_verified_on", sa.Date(), nullable=True),
        sa.Column("config_verified", sa.Boolean(), nullable=True),
        sa.Column("reopened_on", sa.Date(), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("first_seen_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vulnerability_id"], ["vulnerabilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["remediation_id"], ["remediations.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("device_id", "vulnerability_id", name="uq_exposure_device_vuln"),
    )
    op.create_index("ix_device_exposures_device_id", "device_exposures", ["device_id"])
    op.create_index(
        "ix_device_exposures_vulnerability_id", "device_exposures", ["vulnerability_id"]
    )
    op.create_index("ix_device_exposures_status", "device_exposures", ["status"])
    op.create_index("ix_device_exposures_remediation_id", "device_exposures", ["remediation_id"])
    op.create_index("ix_device_exposures_first_seen_on", "device_exposures", ["first_seen_on"])


def downgrade() -> None:
    op.drop_table("device_exposures")
