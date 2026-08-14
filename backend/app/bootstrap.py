"""起動時のスキーマ適用と、デモデータの投入。

運用者が SQL を手で流す必要は無い。表の形は Alembic（版ごとの差分）で当てる。
以前の1台完結は `create_all`（無い表だけ作る）だったが、列の追加はしない。
そのため版を上げても、既存の SQLite が古い形のまま動き続けることがあった。

デモモードのデータは永続しない。実行環境によっては次のリクエストで
別のインスタンスに当たり、書いた内容が消えて見える。画面にもそう表示する。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from sqlalchemy import inspect

from app.config import settings

logger = logging.getLogger(__name__)

_ready = False

# create_all で作られた試用 DB には alembic_version が無い。
# 新しい順に目印を見て、すでに持っている形を stamp してから差分を当てる。
# 今後 create_all は使わないので、公開後の DB はこの表を通らない。
_LEGACY_MARKERS: tuple[tuple[str, str, str | None], ...] = (
    ("c9f1a2b3d4e5", "topology_nodes", "site_id"),
    ("b7e4d12c90aa", "topology_groups", None),
    ("a1b2c3d4e5f6", "sites", "code"),
    ("e6f3c92da41b", "pending_links", None),
    ("a7c3e91b04df", "device_tunnels", None),
    ("d5e2b71fa93c", "links", "a_side"),
    ("c4d1a8f26b57", "vlans", None),
    ("b2c9e14d7f30", "device_vlans", None),
    ("f93b2d5e81a4", "device_exposures", None),
    ("e2f8a41c7b93", "remediations", None),
    ("a17c3b90d4e2", "config_plans", None),
    ("c504e129fb92", "documents", None),
    ("8189614fe799", "product_references", None),
    ("b724b443b51f", "topology_nodes", None),
    ("1da59de2c90b", "scenarios", None),
    ("2845e8b5d803", "change_logs", None),
    ("f3494883da84", "devices", None),
)

_WIPE_HINT = (
    "このデータベースは表があるのに版の記録が無く、どの版か判別できません。"
    "SQL を手で直す必要はありません。"
    "まだ試用中で中身を捨ててよいなら、データフォルダを消して起動し直してください。"
    "1台完結は ~/.netvision（Windows は %USERPROFILE%\\.netvision）。"
    "機器の Config 原本は DB の外にあるはずです。"
)


def _schema_apply_disabled() -> bool:
    """試験はメモリ上の表を自分で作る。起動時の Alembic は通さない。"""
    flag = os.environ.get("NETVISION_SKIP_SCHEMA", "").lower()
    if flag in {"1", "true", "yes"}:
        return True
    return os.environ.get("PYTEST_CURRENT_TEST") is not None


def ensure_ready() -> None:
    """必要ならスキーマを最新にする。デモモードならデモデータも入れる。何度呼んでも安全。"""
    global _ready
    if _ready or _schema_apply_disabled():
        return

    try:
        apply_schema()
        if settings.demo_mode:
            from app.db import engine

            if "devices" in inspect(engine).get_table_names():
                from scripts.seed import seed

                seed()
        _ready = True
    except Exception:
        # 失敗したまま画面を出すと、古い表のまま一部だけ落ちて原因が分からない
        logger.exception("起動時のスキーマ適用に失敗しました")
        raise


def apply_schema() -> None:
    """Alembic を head まで当てる。create_all の試用 DB は版を補ってから差分を当てる。"""
    from alembic import command
    from app.db import engine

    cfg = _alembic_config()
    _stamp_legacy_create_all(engine, cfg)
    command.upgrade(cfg, "head")


def _alembic_config():
    from alembic.config import Config

    backend = Path(__file__).resolve().parents[1]
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    return cfg


def _stamp_legacy_create_all(engine, cfg) -> None:
    """alembic_version が無い既存 DB を、中身から見て stamp する。

    空なら何もしない（upgrade が最初から作る）。
    判別できない形なら、手で SQL を書かせず消し方を示す。
    """
    from alembic import command

    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "alembic_version" in tables or not tables:
        return

    column_cache: dict[str, set[str]] = {}

    def has(table: str, column: str | None) -> bool:
        if table not in tables:
            return False
        if column is None:
            return True
        if table not in column_cache:
            column_cache[table] = {c["name"] for c in inspector.get_columns(table)}
        return column in column_cache[table]

    matched: str | None = None
    for revision, table, column in _LEGACY_MARKERS:
        if has(table, column):
            matched = revision
            break

    if matched is None:
        raise RuntimeError(_WIPE_HINT)

    logger.info("版の記録が無い既存 DB を %s として引き継ぎます", matched)
    command.stamp(cfg, matched)
