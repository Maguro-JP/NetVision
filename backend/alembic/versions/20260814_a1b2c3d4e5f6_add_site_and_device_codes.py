"""拠点と機器に、人が決める管理IDを持たせる

Revision ID: a1b2c3d4e5f6
Revises: e6f3c92da41b
Create Date: 2026-08-14

プロジェクトによっては、既に拠点ID・機器の管理番号で台帳を回している。
内部の連番だけでは合わせられないので、自由に決められる一意の文字列を持つ。
空は未設定（NULL）とし、空どうしはぶつからない。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1b2c3d4e5f6"
down_revision = "e6f3c92da41b"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("sites") as batch:
        batch.add_column(sa.Column("code", sa.String(length=80), nullable=True))
        batch.create_index("ix_sites_code", ["code"], unique=True)
    with op.batch_alter_table("devices") as batch:
        batch.add_column(sa.Column("code", sa.String(length=80), nullable=True))
        batch.create_index("ix_devices_code", ["code"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch:
        batch.drop_index("ix_devices_code")
        batch.drop_column("code")
    with op.batch_alter_table("sites") as batch:
        batch.drop_index("ix_sites_code")
        batch.drop_column("code")
