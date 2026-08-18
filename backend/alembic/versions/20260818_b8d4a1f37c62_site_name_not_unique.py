"""同じ名前の拠点を持てるようにする。

拠点の同一性は拠点IDで決まる。名前は呼び名なので、離れた場所に同じ名前の
拠点があること自体は珍しくない（同名のビル、同名の営業所）。名前に重複禁止を
掛けていたため、拠点IDで区別できている台帳を取り込めなかった。

拠点IDの重複禁止はそのまま残す。IDが無い拠点どうしは名前でしか見分けられない
ので、そちらは取り込みと画面で見張る。

Revision ID: b8d4a1f37c62
Revises: a4e7f2c19b53
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "b8d4a1f37c62"
down_revision = "a4e7f2c19b53"
branch_labels = None
depends_on = None


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    return {index["name"] for index in sa.inspect(bind).get_indexes(table)}


def upgrade() -> None:
    names = _index_names("sites")
    with op.batch_alter_table("sites") as batch:
        for name in ("ix_sites_name", "sites_name_key", "uq_sites_name"):
            if name in names:
                batch.drop_index(name)
        batch.create_index("ix_sites_name", ["name"], unique=False)


def downgrade() -> None:
    names = _index_names("sites")
    with op.batch_alter_table("sites") as batch:
        if "ix_sites_name" in names:
            batch.drop_index("ix_sites_name")
        batch.create_index("ix_sites_name", ["name"], unique=True)
