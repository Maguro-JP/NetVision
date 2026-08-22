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


def _announce_pending(engine) -> None:  # noqa: ANN001  Engine（起動を重くしない）
    """表の更新が要るときだけ、要る分だけを知らせる。

    何も要らないときに版を並べても読み飛ばされるだけで、本当に更新が要る
    ときの1行まで一緒に読み飛ばされる。
    """
    from app.schema_alembic import pending_steps

    steps = pending_steps(engine)
    if not steps:
        return
    logger.info("表の更新が %d 件あります", len(steps))
    for step in steps:
        logger.info("  - %s", step["summary"])


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
            # まっさらなので、全部の版が順に当たる。その一覧を出しても
            # 初めて使う人には何の情報でもないので、黙って作って1行で言う
            apply_schema(quiet=True)
            logger.info("データの置き場所を作りました")
            if settings.demo_mode and "devices" in inspect(engine).get_table_names():
                from scripts.seed import seed

                seed()
        else:
            _announce_pending(engine)

        if current_revision(engine) is None:
            inferred = infer_legacy_revision(engine)
            if inferred == head_revision():
                # 列は揃っていて版の記録だけ無い。データの形は変わらないので
                # ここで付ける。人には何も起きていないので、黙って付ける
                apply_schema(quiet=True)
        _ready = True
    except ModuleNotFoundError as exc:
        if exc.name == "alembic":
            # 持ち出し用の一式に Alembic が入っていないと、ここで初めて分かる。
            # 追跡情報だけ出しても、受け取った人には何を足せばよいか伝わらない
            logger.error(
                "表を作る仕組み（Alembic）が見つかりません。"
                "配布物の作り方が古い可能性があります。"
                "lib フォルダに alembic と mako、backend フォルダに "
                "alembic.ini と alembic を入れた一式を作り直してください"
            )
        else:
            logger.exception("起動時のスキーマ適用に失敗しました")
        if settings.demo_mode:
            return
        raise
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
