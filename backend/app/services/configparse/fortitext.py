"""FortiOS の `config … / edit … / set … / next / end` を読むための道具。

FortiOS の設定は、どの機能でも同じ形をしている。読む側で毎回同じ入れ子の
数え方を書くと、そこだけ少しずつ違うものが増える。数え方は1か所に置く。
"""

from __future__ import annotations

import re
from collections.abc import Iterator

_EDIT = re.compile(r'^edit\s+"?([^"]+)"?\s*$', re.IGNORECASE)
_SET = re.compile(r"^set\s+(\S+)\s+(.*)$", re.IGNORECASE)


def blocks(text: str, section: str) -> Iterator[tuple[str, dict[str, str]]]:
    """`config <section>` の中の `edit` ごとに (名前, 設定) を返す。

    入れ子（`config` が中でもう一度出てくる）は飛ばす。中の設定まで拾うと、
    どちらの階層のものか分からなくなるため。値の引用符は外して返す。
    """
    wanted = f"config {section}".lower()
    inside = False
    depth = 0
    name: str | None = None
    values: dict[str, str] = {}

    for raw in text.splitlines():
        line = raw.strip()
        lowered = line.lower()
        if not inside:
            if lowered == wanted:
                inside, depth, name, values = True, 0, None, {}
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
            inside, name, values = False, None, {}
            continue
        if depth:
            continue

        edit = _EDIT.match(line)
        if edit:
            if name is not None:
                yield name, values
            name, values = edit.group(1), {}
            continue
        if lowered == "next":
            if name is not None:
                yield name, values
            name, values = None, {}
            continue

        setting = _SET.match(line)
        if setting and name is not None:
            values[setting.group(1).lower()] = setting.group(2).strip().strip('"')

    if inside and name is not None:
        yield name, values


def words(value: str | None) -> list[str]:
    """`set srcintf "a" "b"` のように、値が複数並ぶものを分ける。"""
    if not value:
        return []
    return [item.strip('"') for item in value.split() if item.strip('"')]
