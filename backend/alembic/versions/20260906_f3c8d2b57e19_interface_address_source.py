"""アドレスを固定で書いていない口に、その理由を持たせる。

DHCP で貰う・他の口から借りる（unnumbered）・相手と決める、はどれも
「アドレスが無い」ではない。空欄のままだと、付け忘れと見分けが付かず、
ポート表を見た人が誤って読む。

Revision ID: f3c8d2b57e19
Revises: e7b3c5a19d42
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "f3c8d2b57e19"
down_revision = "e7b3c5a19d42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        batch.add_column(sa.Column("address_source", sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("interfaces") as batch:
        batch.drop_column("address_source")
