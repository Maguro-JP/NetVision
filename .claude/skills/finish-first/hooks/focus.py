#!/usr/bin/env python3
"""SessionStart と UserPromptSubmit のフック: 今の成果物を1行で差し込む。

docs/reports/deliverables.md の先頭の未完成の行を「今の成果物・完成条件・残り」にして出す。
長い会話や圧縮のあとで、何を完成させようとしていたかが文脈から落ちるのを防ぐ。
表が無いリポジトリでは何も出さない。

環境変数:
  FINISH_FIRST_OFF=1   止める
"""

import os
import re
import sys
from pathlib import Path

TABLE = Path("docs/reports/deliverables.md")
DONE = ("完成", "中止")
# 完成条件として弱い言い方。確かめ方（コマンド、テスト、出る結果）が無ければ注意する
WEAK = re.compile(r"(動く|動作する|対応する|改善する|良くする|使える|完成する|実装する)（?仮?）?$")
CHECKABLE = re.compile(r"`|テスト|コマンド|通る|出る|表示|返す|になる|できる|spec|test")


def rows(text: str):
    out = []
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 5 or cells[0] in ("#", "") or set(cells[0]) <= set("-: "):
            continue
        out.append(cells)
    return out


def main() -> int:
    if os.environ.get("FINISH_FIRST_OFF") == "1" or not TABLE.is_file():
        return 0
    try:
        table = rows(TABLE.read_text(encoding="utf-8"))
    except OSError:
        return 0
    if not table:
        return 0
    current = next((r for r in table if not r[3].startswith(DONE)), None)
    if current is None:
        print(f"成果物の表（{TABLE}）に未完成はない。新しい成果物は、完成条件を書いてから始める（finish-first）。")
        return 0
    num, name, cond, state, rest = current[0], current[1], current[2], current[3], current[4]
    lines = [f"今の成果物 {num}: {name}（{state}）。完成条件: {cond}。残り: {rest}。これが完成するまで新しい成果物を始めない（finish-first）。"]
    if WEAK.search(cond) and not CHECKABLE.search(cond):
        lines.append(f"注意: 成果物 {num} の完成条件が、動かして確かめられる文になっていない。コマンド名かテスト名か、出る結果を書く。")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
