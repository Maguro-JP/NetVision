"""枠を入れ子にできるようにする。

ラックの枠だけでは、その機材がどのフロア・どの部屋にあるのかが図から
分からない。ラックの枠を、部屋やフロアの枠で更に囲めるようにする。

親は同じ枠の表（自己参照）で持つ。別の表にすると、枠の付け替えのたびに
2つの表を揃えることになり、片方だけ残った枠が出る。

Revision ID: e5b2c7d1a934
Revises: d3a7c1e5b982
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e5b2c7d1a934"
down_revision = "d3a7c1e5b982"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("topology_groups", sa.Column("parent_id", sa.Integer(), nullable=True))
    op.create_index("ix_topology_groups_parent_id", "topology_groups", ["parent_id"])
    # 親の枠を消したら、中の枠は外側の枠が無くなるだけで残す。一緒に消すと、
    # ラックの枠まで巻き添えで消える。
    # SQLite は後から自分自身への外部キーを足せない（表を作り直すことになり、
    # 既にある枠を運び直す必要が出る）。持ち出し用の SQLite では、この関係は
    # 読み書きするときに確かめる
    if op.get_bind().dialect.name != "sqlite":
        op.create_foreign_key(
            "fk_topology_groups_parent",
            "topology_groups",
            "topology_groups",
            ["parent_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("fk_topology_groups_parent", "topology_groups", type_="foreignkey")
    op.drop_index("ix_topology_groups_parent_id", table_name="topology_groups")
    op.drop_column("topology_groups", "parent_id")
