"""スキーマで共通に使う型。

## 時刻

DB には UTC で持つが、そのまま JSON に出すとタイムゾーンの付かない文字列になる。
受け取ったブラウザはそれを現地時刻と解釈するので、日本では9時間ずれる。
`2026-08-09T13:37:30` を JST の 13:37 と読んでしまい、実際は 22:37。

見た目が「それらしい時刻」になるので、ずれていることに気づきにくい。
出す側で UTC だと明示して、変換はブラウザに任せる。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import PlainSerializer


def _with_timezone(value: datetime) -> str:
    """タイムゾーンの付かない値は UTC として出す。"""
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return aware.isoformat()


# タイムゾーン付きで返る日時。画面側は現地時刻に直して表示する
Timestamp = Annotated[datetime, PlainSerializer(_with_timezone, return_type=str)]


def refuse_null(value: object) -> object:
    """PATCH で「送らない」と「空にする」を分ける。

    更新用のスキーマは項目を省略できるよう `X | None` で持つが、DB の列が
    空を許さない項目（機器名・状態など）に null が来ると、そのまま書こうとして
    NOT NULL 違反の 500 になる。null を送られたら 422 で断る。省略は今までどおり。
    `field_validator(..., mode="before")` から呼ぶ。
    """
    if value is None:
        raise ValueError("空にはできない項目です。変えないときは送らないでください")
    return value
