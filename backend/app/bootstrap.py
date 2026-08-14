"""起動時のスキーマ適用と、デモデータの投入。

空の DB とデモは、ここで最新の形にする。中身のある DB に差分があるときは
当てず、専用ページで控えを取ってから当てる。
"""

from __future__ import annotations

import logging
import os

from sqlalchemy import inspect

from app.config import settings

logger = logging.getLogger(__name__)

_ready = False


def _schema_apply_disabled() -> bool:
    """試験はメモリ上の表を自分で作る。起動時の Alembic は通さない。"""
    flag = os.environ.get("NETVISION_SKIP_SCHEMA", "").lower()
    if flag in {"1", "true", "yes"}:
        return True
    return os.environ.get("PYTEST_CURRENT_TEST") is not None


def ensure_ready() -> None:
    """空なら表を作る。デモならデモデータも入れる。差分がある既存 DB は触らない。"""
    global _ready
    if _ready or _schema_apply_disabled():
        return

    try:
        from app.db import engine
        from app.schema_alembic import (
            apply_schema,
            current_revision,
            head_revision,
            infer_legacy_revision,
            is_empty,
        )

        if settings.demo_mode or is_empty(engine):
            apply_schema()
            if settings.demo_mode and "devices" in inspect(engine).get_table_names():
                from scripts.seed import seed

                seed()
        elif current_revision(engine) is None:
            inferred = infer_legacy_revision(engine)
            if inferred == head_revision():
                # 列は揃っていて版の記録だけ無い。データの形は変わらないのでここで付ける
                apply_schema()
        _ready = True
    except Exception:
        logger.exception("起動時のスキーマ適用に失敗しました")
        # デモ（Vercel プレビュー）は画面が真っ白になる方が困るので落とさない。
        if settings.demo_mode:
            return
        raise


def apply_schema() -> None:
    """試験と互換のため、実体は schema_alembic に置く。"""
    from app.schema_alembic import apply_schema as _apply

    _apply()


def _alembic_config():
    from app.schema_alembic import alembic_config

    return alembic_config()
