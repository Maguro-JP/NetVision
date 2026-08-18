"""資料の中身を、章とコマンドに分けて持つ。

PDF やテキストを1つのファイルとして置いておくだけでは、「この機種のこの
コマンドはどう書くのか」を引けない。渡された資料をこちらで章に切り、
章ごとに出てくるコマンドを抜き出して、機器・型番から辿れるようにする。

本文もそのまま持つ。閉域では外の検索に出せないので、探すのはこの表の中で
完結させる必要がある。

Revision ID: a4e7f2c19b53
Revises: f1c3d8a25e07
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "a4e7f2c19b53"
down_revision = "f1c3d8a25e07"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "document_sections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "document_id",
            sa.Integer(),
            sa.ForeignKey("documents.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        # 資料の中での並び。章を頭から追えるようにする
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading", sa.String(400), nullable=True),
        sa.Column("page", sa.Integer(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False, server_default=""),
        # その章に出てくるコマンド。改行区切りで持つ（章から引くための見出し）
        sa.Column("commands", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_document_sections_page", "document_sections", ["document_id", "position"])

    # 整理の状態。取り込んだが読めなかった資料を見分ける
    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("indexed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("index_note", sa.String(300), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_column("index_note")
        batch.drop_column("indexed_at")
    op.drop_index("ix_document_sections_page", table_name="document_sections")
    op.drop_table("document_sections")
