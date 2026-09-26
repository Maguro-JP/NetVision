"""Excel のセルに書けない文字を落とす。

openpyxl は制御文字（タブ・改行・復帰を除く）を含む文字列をセルに入れると
IllegalCharacterError を投げる。端末のログから写した Config の ESC などが
機器名や説明に1つ混じっているだけで、台帳の書き出し全体が失敗してしまう。
書き出す直前に落とし、残りの内容はそのまま出す。

もう1つ、openpyxl は "=" で始まる文字列を式として、"#N/A" のような形の文字列を
エラー値として書く。備考の「=== 要確認 ===」は壊れた式になり、
「=HYPERLINK(...)」は開いた人の Excel でリンクとして動く。
CellRichText で包んだ値だけは型の推定を通らず文字列として書かれ、
読み戻すと元の文字列に戻るので、内容を変えずに防げる。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

#: openpyxl が文字列をエラー値として書く形（openpyxl.cell.cell.ERROR_CODES と同じ）
ERROR_CODES = frozenset(("#NULL!", "#DIV/0!", "#VALUE!", "#REF!", "#NAME?", "#NUM!", "#N/A"))

#: openpyxl の ILLEGAL_CHARACTERS_RE と同じ範囲。openpyxl は書き出すときだけ読み込む
#: 作りにしてあるので、ここで import せずに同じものを持つ（一致は試験で見ている）
ILLEGAL_CHARACTERS_RE = re.compile(r"[\000-\010]|[\013-\014]|[\016-\037]")


def cell_value(value: Any) -> Any:
    """文字列なら書けない文字を落とし、式やエラー値として読まれる形なら文字列として包む。

    数値・日付などはそのまま返す。
    """
    if isinstance(value, str):
        text = ILLEGAL_CHARACTERS_RE.sub("", value)
        if text.startswith("=") or text in ERROR_CODES:
            from openpyxl.cell.rich_text import CellRichText

            return CellRichText(text)
        return text
    return value


def row_values(values: Iterable[Any]) -> list[Any]:
    """1行分をまとめて通す。"""
    return [cell_value(value) for value in values]
