"""ポートに書いた OSPF の決めごとを持たせる。

エリア・hello / dead・線の見立て・合言葉の掛け方が両端で食い違うと、
隣どうしとして認識しない。上がらないので気づきはするが、原因がここだと
分かるまでが長い。読み取って持っておき、両端で突き合わせられるようにする。

Revision ID: d2a6f9b41c85
Revises: c1f5e8a37d20
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "d2a6f9b41c85"
down_revision = "c1f5e8a37d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        batch.add_column(sa.Column("ospf_area", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("ospf_hello", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ospf_dead", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("ospf_network", sa.String(length=20), nullable=True))
        batch.add_column(sa.Column("ospf_auth", sa.String(length=10), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        for name in ("ospf_auth", "ospf_network", "ospf_dead", "ospf_hello", "ospf_area"):
            batch.drop_column(name)
