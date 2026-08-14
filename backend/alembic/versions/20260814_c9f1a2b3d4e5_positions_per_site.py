"""配置座標を拠点の図ごとに持つ。

Revision ID: c9f1a2b3d4e5
Revises: b7e4d12c90aa
Create Date: 2026-08-14

機器1台に座標が1つだと、本社の図で置いた対向が支社の図でも同じ場所に出る。
見ている拠点の図ごとに残す。既存の行は全体図（site_id 空）として残す。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "c9f1a2b3d4e5"
down_revision = "b7e4d12c90aa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("topology_nodes") as batch:
        batch.add_column(sa.Column("site_id", sa.Integer(), nullable=True))
        batch.create_foreign_key(
            "fk_topology_nodes_site_id",
            "sites",
            ["site_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch.drop_constraint("uq_topology_node", type_="unique")
    op.create_index("ix_topology_nodes_site_id", "topology_nodes", ["site_id"])
    op.create_index(
        "uq_topology_node_site",
        "topology_nodes",
        ["device_id", "layer", "site_id"],
        unique=True,
        sqlite_where=sa.text("site_id IS NOT NULL"),
        postgresql_where=sa.text("site_id IS NOT NULL"),
    )
    op.create_index(
        "uq_topology_node_global",
        "topology_nodes",
        ["device_id", "layer"],
        unique=True,
        sqlite_where=sa.text("site_id IS NULL"),
        postgresql_where=sa.text("site_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_topology_node_global", table_name="topology_nodes")
    op.drop_index("uq_topology_node_site", table_name="topology_nodes")
    op.drop_index("ix_topology_nodes_site_id", table_name="topology_nodes")
    with op.batch_alter_table("topology_nodes") as batch:
        batch.drop_constraint("fk_topology_nodes_site_id", type_="foreignkey")
        batch.drop_column("site_id")
        batch.create_unique_constraint("uq_topology_node", ["device_id", "layer"])
