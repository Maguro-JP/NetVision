"""拠点に、いまどういう状態かを持たせる。

拠点は名前と住所だけでは足りない。移転で住所だけ変わることもあれば、
名前はそのままで一時閉鎖・閉店になることもある。機器を消さずに
「この拠点はいま使っていない」と分かるようにする。

閉じた日・開いた日も持つ。いつ閉めたのかが分からないと、撤去が済んで
いない機器の棚卸しができない。

Revision ID: f1c3d8a25e07
Revises: e5b2c7d1a934
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f1c3d8a25e07"
down_revision = "e5b2c7d1a934"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sites") as batch:
        batch.add_column(
            sa.Column("status", sa.String(20), nullable=False, server_default="active")
        )
        batch.add_column(sa.Column("opened_on", sa.Date(), nullable=True))
        batch.add_column(sa.Column("closed_on", sa.Date(), nullable=True))
    op.create_index("ix_sites_status", "sites", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sites_status", table_name="sites")
    with op.batch_alter_table("sites") as batch:
        batch.drop_column("closed_on")
        batch.drop_column("opened_on")
        batch.drop_column("status")
