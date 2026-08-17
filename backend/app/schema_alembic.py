"""Alembic の設定と、表の差分を当てる処理。

運用者が SQL を手で流す必要は無い。空の DB とデモは起動時に当てる。
中身のある DB の差分は、控えを取ってから専用ページで当てる。
"""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# create_all で作られた試用 DB には alembic_version が無い。
# 新しい順に目印を見て、すでに持っている形を stamp してから差分を当てる。
LEGACY_MARKERS: tuple[tuple[str, str, str | None], ...] = (
    ("f1c3d8a25e07", "sites", "status"),
    ("e5b2c7d1a934", "topology_groups", "parent_id"),
    ("d3a7c1e5b982", "devices", "label"),
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

WIPE_HINT = (
    "このデータベースは表があるのに版の記録が無く、どの版か判別できません。"
    "台帳はそのまま置いておいてください。SQL を手で直す必要もありません。"
    "先にデータフォルダごと控えを取ってください"
    "（1台完結は ~/.netvision、Windows は %USERPROFILE%\\.netvision）。"
    "控えを取ったあとであれば、中身を捨ててよいと判断できる場合にかぎり"
    "作り直せます。中に台帳が入っているなら消さないでください。"
)


def alembic_config():
    from alembic.config import Config

    backend = Path(__file__).resolve().parent.parent
    cfg = Config(str(backend / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend / "alembic"))
    return cfg


def table_names(engine: Engine) -> set[str]:
    return set(inspect(engine).get_table_names())


def is_empty(engine: Engine) -> bool:
    names = table_names(engine)
    return not names or names == {"alembic_version"}


def current_revision(engine: Engine) -> str | None:
    from sqlalchemy import text

    try:
        with engine.connect() as connection:
            return connection.execute(text("select version_num from alembic_version")).scalar()
    except Exception:
        return None


def head_revision() -> str:
    from alembic.script import ScriptDirectory

    script = ScriptDirectory.from_config(alembic_config())
    head = script.get_current_head()
    if not head:
        raise RuntimeError("マイグレーションの最新版が見つかりません")
    return head


def infer_legacy_revision(engine: Engine) -> str | None:
    """alembic_version が無い DB の、いまの形に相当する版。空なら None。"""
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if not tables or "alembic_version" in tables:
        return None

    column_cache: dict[str, set[str]] = {}

    def has(table: str, column: str | None) -> bool:
        if table not in tables:
            return False
        if column is None:
            return True
        if table not in column_cache:
            column_cache[table] = {c["name"] for c in inspector.get_columns(table)}
        return column in column_cache[table]

    for revision, table, column in LEGACY_MARKERS:
        if has(table, column):
            return revision
    return None


def stamp_legacy_create_all(engine: Engine, cfg) -> None:
    from alembic import command

    if "alembic_version" in table_names(engine) or is_empty(engine):
        return

    matched = infer_legacy_revision(engine)
    if matched is None:
        raise RuntimeError(WIPE_HINT)

    logger.info("版の記録が無い既存 DB を %s として引き継ぎます", matched)
    command.stamp(cfg, matched)


def apply_schema() -> None:
    """Alembic を head まで当てる。create_all の試用 DB は版を補ってから差分を当てる。"""
    from alembic import command
    from app.db import engine

    cfg = alembic_config()
    stamp_legacy_create_all(engine, cfg)
    command.upgrade(cfg, "head")


def pending_steps(engine: Engine) -> list[dict[str, str]]:
    """いまの版から最新までに当たる差分。新しい順ではなく当たる順。"""
    from alembic.script import ScriptDirectory

    cfg = alembic_config()
    script = ScriptDirectory.from_config(cfg)
    head = script.get_current_head()
    current = current_revision(engine)
    if current is None:
        inferred = infer_legacy_revision(engine)
        current = inferred

    if is_empty(engine) or not head or current == head:
        return []

    # iterate_revisions は上から下へ出す。当たる順に直す
    revisions = list(script.iterate_revisions(head, current))
    revisions.reverse()
    steps: list[dict[str, str]] = []
    for revision in revisions:
        summary = (revision.doc or "").strip().split("\n", 1)[0].strip()
        steps.append({"revision": revision.revision, "summary": summary or revision.revision})
    return steps
