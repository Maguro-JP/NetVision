"""ログイン時に見せる文言（banner）を、設定と間違えないようにする。

banner の中は、機器にとってはただの文字列である。だが行だけを見ると
設定と見分けが付かない。実際、注意書きに設定例を書いてある機器は多い。

```
banner motd ^C
  この機器は許可された者だけが使えます。
  例) interface GigabitEthernet0/99 は使用禁止
^C
```

これをそのまま読むと、無い口が1つ増える。増えた口は台帳に載り、
指摘にも出る。「無いものを有ると言う」いちばん困る間違い方なので、
読む前に外しておく。

### 決めごと

- 閉じの印が見つからないときは、何も外さない。閉じを探しに行った先が
  設定本体だと、設定ぜんぶを捨てることになる
- 印が書かれていない書き方（Aruba CX など）は触らない。どこまでが文言かを
  決められないものを、当てずっぽうで切らない
"""

from __future__ import annotations

import re

#: `banner motd ^C` の形。印は種別の後ろに書かれる。
#: 種別だけの `banner motd` は、印が無いので触らない（どこまでが文言かを決められない）
_OPEN = re.compile(r"^\s*banner\s+[\w-]+\s+(\S+)\s*$", re.IGNORECASE)


def strip(text: str) -> str:
    """banner の中身を空行に置き換える。行数は変えない。

    行数を変えると、行番号で場所を示している読み手とずれる。
    """
    lines = text.splitlines()
    out = list(lines)
    index = 0
    while index < len(lines):
        found = _OPEN.match(lines[index])
        if found is None:
            index += 1
            continue

        mark = found.group(1)
        close = _close(lines, index + 1, mark)
        if close is None:
            # 閉じが無い。ここから先を全部捨てると設定本体まで消える
            index += 1
            continue

        for position in range(index, close + 1):
            out[position] = ""
        index = close + 1

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def _close(lines: list[str], start: int, mark: str) -> int | None:
    for position in range(start, len(lines)):
        if mark in lines[position]:
            return position
    return None
