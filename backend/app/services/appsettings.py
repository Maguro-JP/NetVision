"""画面から切り替える設定。

環境変数は起動のときに決まるもので、動かしながら切り替えることができない。
モデルを使うかどうかのように、その場で切って試したいものは、ここに持つ。

置き場所は DB ではなくファイルにする。表の更新（マイグレーション）を通さずに
足せるほうが、この手の小さい設定には向いている。数も増えない前提で、
1つの JSON にまとめる。

環境変数との関係は「画面で決めたことが勝つ」。環境変数は初期値として扱う。
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from app.config import settings

logger = logging.getLogger(__name__)

FILE_NAME = "settings.json"


def path() -> Path:
    """設定ファイルの置き場所。データの隣に置く。"""
    return Path(settings.document_storage_path).expanduser().resolve().parent / FILE_NAME


def load() -> dict[str, Any]:
    """読めなければ空。壊れていても道具は止めない。"""
    file = path()
    if not file.is_file():
        return {}
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("設定ファイルを読めません（%s）。既定で続けます", file)
        return {}
    return data if isinstance(data, dict) else {}


def get(key: str, default: Any = None) -> Any:
    return load().get(key, default)


#: 画面から決められるもの。ここに無いものは環境変数のまま
#: （DB の接続先や待ち受けアドレスのように、起動時にしか決められないもの、
#: 間違えると外から見える状態になるものは、画面に出さない）
EDITABLE: dict[str, type] = {
    "llm_enabled": bool,
    "warning_days": int,
    "critical_days": int,
    "reference_stale_days": int,
    "display_timezone": str,
    "model_dir": str,
}


def number(key: str, fallback: int) -> int:
    """画面で決めた数。決めていなければ環境変数の値。"""
    value = load().get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return fallback
    return value


def text(key: str, fallback: str) -> str:
    """画面で決めた文字列。空や未設定なら環境変数の値。"""
    value = load().get(key)
    return value if isinstance(value, str) and value.strip() else fallback


def set_value(key: str, value: Any) -> None:
    """1つ書き換える。書けなければ、その旨を記録して黙って諦めない。"""
    data = load()
    data[key] = value
    file = path()
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"設定を保存できませんでした（{file}）") from error
