"""外へ出す前に伏せる。

実機の Config には、管理IP・SNMP のコミュニティ名・鍵・パスワードが入っている。
どの事業者を使うにせよ、そのまま送ってよいものではない。送る直前にここを通し、
伏せ字に置き換える。

伏せたものは数だけ記録に残す。何を伏せたかを残すと、記録そのものが漏れ口に
なるため。

伏せ方は「消す」ではなく「同じ値は同じ印にする」。同じアドレスが何度も出てくる
Config で、全部を同じ印にしてしまうと、関係が読めなくなって答えが役に立たない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: 伏せる対象。名前は記録に出るので、中身が分からないものにしておく
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "secret",
        re.compile(
            r"(?i)\b(password|passwd|secret|key|community|pre-shared-key|psk)\b\s+"
            r"(?:\d+\s+)?(\S+)"
        ),
    ),
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
)

#: 伏せない値。伏せると文の意味が変わるもの（既定経路、自分自身）
_KEEP = {"0.0.0.0", "127.0.0.1"}


def _is_mask(value: str) -> bool:
    """サブネットマスクか。伏せると「/24 なのか /30 なのか」が消え、
    返ってくる設定が使い物にならなくなる。秘密でもないので残す。"""
    return value.startswith("255.")


@dataclass
class Masked:
    text: str
    #: 何をいくつ伏せたか。中身は残さない
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def mask(text: str) -> Masked:
    """伏せた文と、伏せた数を返す。"""
    counts: dict[str, int] = {}
    seen: dict[str, str] = {}

    def label(kind: str, value: str) -> str:
        if value in seen:
            return seen[value]
        counts[kind] = counts.get(kind, 0) + 1
        name = f"<{kind}-{counts[kind]}>"
        seen[value] = name
        return name

    def hide_secret(match: re.Match[str]) -> str:
        value = match.group(2)
        return match.group(0).replace(value, label("secret", value))

    def hide_ip(match: re.Match[str]) -> str:
        value = match.group(0)
        if value in _KEEP or _is_mask(value):
            return value
        return label("ipv4", value)

    masked = _PATTERNS[0][1].sub(hide_secret, text)
    masked = _PATTERNS[1][1].sub(hide_ip, masked)
    return Masked(text=masked, counts=counts)
