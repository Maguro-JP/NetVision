"""モデルへ送った記録。

外へ出す以上、いつ・どこへ・どれだけ送ったかを説明できないといけない。
情シスに聞かれて答えられないと、その時点で使用は止まる。

残すのは「送ったという事実」だけで、送った中身は残さない。中身を残すと、
記録そのものが漏れ口になる。伏せた数は残す（何件伏せたかは説明に要る）。

置き場所はデータの隣。DB に入れないのは、表の更新を通さずに足せるようにする
ため。1行1件の JSON で、古いものから消えないよう追記だけにする。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path

from app.config import settings

logger = logging.getLogger(__name__)

FILE_NAME = "llm-usage.log"


def path() -> Path:
    return Path(settings.document_storage_path).expanduser().resolve().parent / FILE_NAME


def record(
    *,
    role: str,
    provider: str,
    url: str,
    model: str,
    local: bool,
    sent_chars: int,
    masked: dict[str, int],
    answered: bool,
) -> None:
    """1件書く。書けなくても、道具そのものは止めない。"""
    entry = {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "role": role,
        "provider": provider,
        # 鍵や経路そのものは残さない。どこへ出したかが分かれば足りる
        "host": urllib.parse.urlparse(url).hostname or "",
        "model": model,
        "local": local,
        "sent_chars": sent_chars,
        "masked": masked,
        "answered": answered,
    }
    try:
        file = path()
        file.parent.mkdir(parents=True, exist_ok=True)
        with file.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError as error:
        logger.warning("送信の記録を書けませんでした（%s）", error)


def recent(limit: int = 100) -> list[dict]:
    """新しい順に読む。画面に出す用。"""
    file = path()
    if not file.is_file():
        return []
    try:
        lines = file.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out = []
    for line in reversed(lines[-limit * 2 :]):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
        if len(out) >= limit:
            break
    return out
