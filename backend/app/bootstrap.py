"""プレビュー環境向けの起動処理。

Vercel のようなサーバレス環境では、あらかじめマイグレーションを流しておく手段が
無い。かといって毎回 DB を用意させると「まず画面を見たい」が重くなる。

そこでデモモードのときだけ、起動時にスキーマを作ってデモデータを入れる。
**本番運用ではこの経路を通さない**（alembic でマイグレーションする）。

デモモードのデータは永続しない。実行環境によっては次のリクエストで
別のインスタンスに当たり、書いた内容が消えて見える。画面にもそう表示する。
"""

from __future__ import annotations

import logging

from sqlalchemy import inspect

from app.config import settings

logger = logging.getLogger(__name__)

_ready = False


def ensure_ready() -> None:
    """デモモードなら、テーブルとデモデータを用意する。何度呼んでも安全。"""
    global _ready
    if _ready or not settings.demo_mode:
        return

    from app.db import Base, engine

    try:
        import app.models  # noqa: F401  すべてのモデルを Base に登録する

        Base.metadata.create_all(engine)

        # 既にデータがあれば触らない
        if "devices" in inspect(engine).get_table_names():
            from scripts.seed import seed

            seed()
        _ready = True
    except Exception:  # プレビューが真っ白になる方が困るので落とさない
        logger.exception("デモデータの用意に失敗しました")
