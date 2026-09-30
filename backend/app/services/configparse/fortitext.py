"""FortiOS の `config … / edit … / set … / next / end` を読むための道具。

FortiOS の設定は、どの機能でも同じ形をしている。読む側で毎回同じ入れ子の
数え方を書くと、そこだけ少しずつ違うものが増える。数え方は1か所に置く。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_EDIT = re.compile(r'^edit\s+"?([^"]+)"?\s*$', re.IGNORECASE)
_SET = re.compile(r"^set\s+(\S+)\s+(.*)$", re.IGNORECASE)
#: 並べて書く値の1つ分。引用符で囲めば中に空白を含められる
#: （Administration Guide の例: `set groups "NTLM-FSSO-Group" "Ldap-Group"`、
#: `set name "PC to Google"`）
_ITEM = re.compile(r'"([^"]*)"|(\S+)')


class Settings(dict[str, str]):
    """`edit` 1つ分の設定。値は引用符を外したもの、`raw` は書いてあったままの値。

    並べて書く値（`set service "Windows AD" "DNS"`）は、引用符を外すと区切りが分からなくなる。
    分けるときは `items` で `raw` から分ける。
    """

    def __init__(self) -> None:
        super().__init__()
        self.raw: dict[str, str] = {}


def blocks(text: str, section: str) -> Iterator[tuple[str, Settings]]:
    """`config <section>` の中の `edit` ごとに (名前, 設定) を返す。

    入れ子（`config` が中でもう一度出てくる）は飛ばす。中の設定まで拾うと、
    どちらの階層のものか分からなくなるため。値の引用符は外して返す。
    """
    wanted = f"config {section}".lower()
    inside = False
    depth = 0
    name: str | None = None
    values = Settings()

    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if not inside:
            if lowered == wanted:
                inside, depth, name, values = True, 0, None, Settings()
            continue

        if lowered.startswith("config "):
            depth += 1
            continue
        if lowered == "end":
            if depth:
                depth -= 1
                continue
            if name is not None:
                yield name, values
            inside, name, values = False, None, Settings()
            continue
        if depth:
            continue

        edit = _EDIT.match(line)
        if edit:
            if name is not None:
                yield name, values
            name, values = edit.group(1), Settings()
            continue
        if lowered == "next":
            if name is not None:
                yield name, values
            name, values = None, Settings()
            continue

        setting = _SET.match(line)
        if setting and name is not None:
            key, value = setting.group(1).lower(), setting.group(2).strip()
            values[key] = value.strip('"')
            values.raw[key] = value

    if inside and name is not None:
        yield name, values


def items(values: Settings, key: str) -> list[str]:
    """並べて書く値を1つずつに分ける。引用符の中の空白では分けない。"""
    raw = values.raw.get(key)
    if not raw:
        return []
    return [quoted or bare for quoted, bare in _ITEM.findall(raw) if quoted or bare]
