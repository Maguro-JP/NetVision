"""機器と製品リファレンスに、型番の突き合わせ用の鍵を持たせる。

Cisco は同じハードを `WS-C2960X-48FPD-L` とも `C2960X-48FPD-L` とも、
`C9300-48P` とも `C9300-48P-A` とも印字する。型番の文字列そのもので結合すると、
型番単位で持つ EoL が現場の機器に当たらない。飾りを外した鍵で結合する。

Revision ID: a1c4e7b29d63
Revises: f3c8d2b57e19
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a1c4e7b29d63"
down_revision = "f3c8d2b57e19"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("devices", "product_references"):
        with op.batch_alter_table(table) as batch:
            batch.add_column(
                sa.Column("model_key", sa.String(length=120), nullable=False, server_default="")
            )
            batch.create_index(f"ix_{table}_model_key", ["model_key"])

    # 既存の行に鍵を入れる。保存時と同じ関数で作る（規則を2か所に持たない）
    from app.services.versions import model_key

    bind = op.get_bind()
    for table in ("devices", "product_references"):
        rows = bind.execute(sa.text(f"SELECT id, vendor, model FROM {table}")).all()
        for row_id, vendor, model in rows:
            bind.execute(
                sa.text(f"UPDATE {table} SET model_key = :key WHERE id = :id"),
                {"key": model_key(vendor, model), "id": row_id},
            )


def downgrade() -> None:
    for table in ("devices", "product_references"):
        with op.batch_alter_table(table) as batch:
            batch.drop_index(f"ix_{table}_model_key")
            batch.drop_column("model_key")
