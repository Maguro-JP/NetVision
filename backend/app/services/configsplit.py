"""1つのファイルに何台分も入っているとき、台数ぶんに分ける。

現場では、何台かの `show running-config` をまとめて1つのファイルに貼る。
それをそのまま読むと、いちばん最初のホスト名だけが機器名になり、
2台目以降の口と VLAN が1台目に付く。

指摘が1つ増えるより悪い。台帳に、その機器が持っていない口が載るからである。
後で図を引くときも、その口から線が伸びる。

### 決めごと

- 分けるのは、機器名の行が2つ以上あって、名前が違うときだけ。
  1つしか無いものを分けようとしない
- 機器名の行より前に書いてある行（version など）は、1台目のものとして扱う
- 文言（banner）の中の機器名では分けない。読む前に外してから探す
"""

from __future__ import annotations

import re

from app.services.configparse import banners

#: 機器名を宣言する行。行頭に書かれるものだけを見る。
#: 字下げされた行（FortiGate の `set hostname` など）は、囲みの中でしか
#: 意味が決まらないので、ここでは見ない
_DEVICE_NAME = re.compile(
    r"^(?:hostname|sysname|device-name|console\s+prompt)\s+\S"
    r"|^set\s+system\s+host-name\s+\S"
    r"|^set\s+deviceconfig\s+system\s+hostname\s+\S",
    re.IGNORECASE,
)


def positions(text: str) -> list[int]:
    """機器名の行が何行目にあるか。文言の中は数えない。"""
    cleaned = banners.strip(text).splitlines()
    return [index for index, line in enumerate(cleaned) if _DEVICE_NAME.match(line)]


def split(text: str) -> list[str]:
    """台数ぶんに分ける。1台分しか入っていなければ、そのまま1つ返す。"""
    marks = positions(text)
    if len(marks) < 2:
        return [text]

    lines = text.splitlines(keepends=True)
    names = [lines[mark].split()[-1] for mark in marks]
    if len(set(names)) < 2:
        # 同じ機器の中で機器名が2度書かれているだけ。分けるとかえって壊す
        return [text]

    # 1つ目の機器名より前（version など）は、1台目のものとして扱う
    bounds = [0, *marks[1:], len(lines)]
    chunks = ["".join(lines[bounds[i] : bounds[i + 1]]) for i in range(len(bounds) - 1)]
    return [chunk for chunk in chunks if chunk.strip()]
