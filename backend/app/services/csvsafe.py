"""書き出した CSV を Excel で開いたときに、値が式として動かないようにする。

CSV には「この欄は文字」という印が無い。Excel は `=`・`+`・`-`・`@`・タブ・復帰で
始まる欄を式として読むので、機器名に `=HYPERLINK(...)` と入っていれば開いた人の Excel で
リンクとして動き、備考の `=== 要確認 ===` は `#NAME?` になる（CSV インジェクション）。
NetVision は式を書き出すことが無い（どの欄も入っている値そのもの）ので、この形の値は
すべて文字として出す。

xlsx はセルに文字の印を付けられるので値を変えずに防げる（xlsxsafe.py）。CSV では
値の先頭に `'` を1つ付けるしかない。Excel は先頭の `'` を「文字として扱う」印として読み、
画面には出さない。Excel 以外で開くと `'` が見えるので、付けるのは式として読まれる形の
値だけに絞る。`-5` のような負の数と `-` だけの欄（「空」の印）には付けない。

取り込み（tablefile.py）では、ここで付けた形の `'` だけを1つ外す。元々 `'` で始まる値にも
同じ形のときは `'` を足しておくので、書き出して取り込み直しても値は変わらない。
"""

from __future__ import annotations

import csv
import re
from collections.abc import Iterable
from typing import Any

#: Excel が式の始まりとして読む先頭の文字（`-` は別に見る）
_FORMULA_START = ("=", "+", "@", "\t", "\r")
#: `-` で始まっても式にならないもの。負の数と `-` だけの欄
_PLAIN_MINUS = re.compile(r"^-(?:\d+(?:\.\d+)?)?$")


def _read_as_formula(text: str) -> bool:
    if not text:
        return False
    if text.startswith(_FORMULA_START):
        return True
    return text.startswith("-") and _PLAIN_MINUS.match(text) is None


def _guarded(text: str) -> bool:
    """`'` の後ろが式の形（または、さらに `'` を付けた形）になっているか。"""
    return text.startswith("'") and (_read_as_formula(text[1:]) or _guarded(text[1:]))


def guard(value: Any) -> Any:
    """書き出す値。式として読まれる形の文字列だけ、先頭に `'` を付ける。"""
    if isinstance(value, str) and (_read_as_formula(value) or _guarded(value)):
        return "'" + value
    return value


def unguard(text: str) -> str:
    """取り込んだ値。guard で付けた `'` を1つ外す。それ以外の `'` には触らない。"""
    return text[1:] if _guarded(text) else text


class _GuardedWriter:
    """csv.writer と同じ使い方で、欄ごとに guard を通す。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner

    def writerow(self, row: Iterable[Any]) -> Any:
        return self._inner.writerow([guard(value) for value in row])

    def writerows(self, rows: Iterable[Iterable[Any]]) -> None:
        for row in rows:
            self.writerow(row)


def writer(buffer: Any, **kwargs: Any) -> _GuardedWriter:
    """書き出しの CSV は、どれもこれで書く。csv.writer を直接使うと守りが抜ける。"""
    return _GuardedWriter(csv.writer(buffer, **kwargs))
