"""機器が揃う前の結線を預かる表

Revision ID: e6f3c92da41b
Revises: d5e2b71fa93c
Create Date: 2026-08-10

導入時、手元にあるのが結線表だけということがある。機器を登録し終える前でも
取り込めるよう、ホスト名のまま預かっておき、両端が台帳に載った時点で
本物の結線に変える。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "e6f3c92da41b"
down_revision = "a7c3e91b04df"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "pending_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("a_hostname", sa.String(length=120), nullable=False),
        sa.Column("a_interface", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("b_hostname", sa.String(length=120), nullable=False),
        sa.Column("b_interface", sa.String(length=120), nullable=False, server_default=""),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column("source", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint(
            "a_hostname", "a_interface", "b_hostname", "b_interface", name="uq_pending_link"
        ),
    )
    op.create_index("ix_pending_links_a_hostname", "pending_links", ["a_hostname"])
    op.create_index("ix_pending_links_b_hostname", "pending_links", ["b_hostname"])


def downgrade() -> None:
    op.drop_index("ix_pending_links_b_hostname", table_name="pending_links")
    op.drop_index("ix_pending_links_a_hostname", table_name="pending_links")
    op.drop_table("pending_links")
