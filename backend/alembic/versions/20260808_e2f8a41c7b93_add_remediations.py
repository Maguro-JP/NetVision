"""add remediations

Revision ID: e2f8a41c7b93
Revises: a17c3b90d4e2
Create Date: 2026-08-08

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e2f8a41c7b93"
down_revision: str | None = "a17c3b90d4e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "remediations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("vulnerability_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "upgrade",
                "workaround",
                "mitigation",
                "accepted",
                "not_applicable",
                name="remediationkind",
                native_enum=False,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("vendor", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("os_name", sa.String(length=80), nullable=True),
        sa.Column("applies_from", sa.String(length=80), nullable=True),
        sa.Column("applies_to", sa.String(length=80), nullable=True),
        sa.Column("summary", sa.String(length=300), nullable=False),
        sa.Column("fixed_version", sa.String(length=80), nullable=True),
        sa.Column("procedure", sa.Text(), nullable=True),
        sa.Column("source_document_id", sa.Integer(), nullable=True),
        sa.Column("config_template_id", sa.Integer(), nullable=True),
        sa.Column("verified_by", sa.String(length=120), nullable=True),
        sa.Column("verified_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["vulnerability_id"], ["vulnerabilities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["config_template_id"], ["config_templates.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_remediations_vulnerability_id", "remediations", ["vulnerability_id"])
    op.create_index("ix_remediations_kind", "remediations", ["kind"])
    op.create_index("ix_remediations_vendor", "remediations", ["vendor"])
    op.create_index("ix_remediations_model", "remediations", ["model"])
    op.create_index("ix_remediations_source_document_id", "remediations", ["source_document_id"])
    op.create_index("ix_remediations_config_template_id", "remediations", ["config_template_id"])


def downgrade() -> None:
    op.drop_table("remediations")
