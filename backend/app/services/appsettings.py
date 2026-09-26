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


#: 置き場所の解決と中身の読み込みを、変わるまで覚えておく。
#: 保守期限の判定は機器1台ごとに境目の日数をここから引くので、毎回ファイルを
#: 解決して読むと、2万台の書き出しで時間の半分がこれに消えていた
_resolved: tuple[str, Path] | None = None
_cached: tuple[Path, int, int, dict[str, Any]] | None = None


def path() -> Path:
    """設定ファイルの置き場所。データの隣に置く。"""
    global _resolved
    configured = settings.document_storage_path
    if _resolved is None or _resolved[0] != configured:
        folder = Path(configured).expanduser().resolve().parent
        _resolved = (configured, folder / FILE_NAME)
    return _resolved[1]


def load() -> dict[str, Any]:
    """読めなければ空。壊れていても道具は止めない。

    中身は、ファイルの更新時刻と大きさが変わるまで読み直さない。
    画面から書き換えれば更新時刻が変わるので、すぐに効く。返すのは写しなので、
    受け取った側が書き換えても覚えている中身は変わらない。
    """
    global _cached
    file = path()
    try:
        stat = file.stat()
    except OSError:
        return {}
    if _cached is not None and _cached[:3] == (file, stat.st_mtime_ns, stat.st_size):
        return dict(_cached[3])
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.warning("設定ファイルを読めません（%s）。既定で続けます", file)
        return {}
    if not isinstance(data, dict):
        return {}
    _cached = (file, stat.st_mtime_ns, stat.st_size, data)
    return dict(data)


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
    "exposure_sync_hours": int,
    "backup_interval_hours": int,
    "backup_keep": int,
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
    global _cached
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise RuntimeError(f"設定を保存できませんでした（{file}）") from error
    finally:
        # 更新時刻の刻みが粗いファイルシステムでも、書いた直後に古い中身を返さない
        _cached = None
