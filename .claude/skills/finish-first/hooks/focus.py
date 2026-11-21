#!/usr/bin/env python3
"""SessionStart と UserPromptSubmit のフック: 今の成果物を1行で差し込む。

docs/reports/deliverables.md の先頭の未完成の行を「今の成果物・完成条件・残り」にして出す。
長い会話や圧縮のあとで、何を完成させようとしていたかが文脈から落ちるのを防ぐ。
表が無いリポジトリでは何も出さない。

SessionStart では毎回出す。UserPromptSubmit では、10 ターンに 1 回と、行が変わったとき（成果物が進んだ、入れ替わった）だけ出す。
毎ターン同じ文を出すとチャットが流れ、1 ターンの差し込みの目安（1,200 文字）も超えるため。

環境変数:
  FINISH_FIRST_OFF=1      止める
  FINISH_FIRST_EVERY=N    UserPromptSubmit で出し直す間隔（既定 10 ターン）
  FINISH_FIRST_STATE=dir  前に出した行とターンの数の置き場（既定は ~/.claude/state/finish-first）
"""

import hashlib
import json
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


def due(d: dict, text: str) -> bool:
    """今出すか。SessionStart（prompt が無い）は出す。UserPromptSubmit は、行が変わったときと N ターンごとだけ。"""
    base = os.environ.get("FINISH_FIRST_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "finish-first"
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(d.get("session_id") or ""))[:40]
    if not sid:
        return True  # セッションが分からなければ、間引かない
    f = root / f"{sid}.json"
    try:
        st = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    key = hashlib.sha1(text.encode("utf-8"), usedforsecurity=False).hexdigest()[:12]
    ev = str(d.get("hook_event_name") or "")
    is_prompt = ev == "UserPromptSubmit" if ev else "prompt" in d
    turn = int(st.get("turn", 0)) + (1 if is_prompt else 0)
    try:
        every = max(1, int(os.environ.get("FINISH_FIRST_EVERY") or 10))
    except ValueError:
        every = 10
    show = (not is_prompt) or st.get("key") != key or turn % every == 0
    try:
        root.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"turn": turn, "key": key}), encoding="utf-8")
    except OSError:
        pass
    return show


SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|This session is being continued")


def main() -> int:
    if os.environ.get("FINISH_FIRST_OFF") == "1" or not TABLE.is_file():
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        d = {}
    if SYSTEMISH.search(str(d.get("prompt") or "")):
        return 0  # 通知のターンには出さない
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
    weak = bool(WEAK.search(cond) and not CHECKABLE.search(cond))  # 切る前の全文で見る（切ると確かめ方の語が落ちる）
    name = name if len(name) <= 60 else name[:59] + "…"
    cond = cond if len(cond) <= 80 else cond[:79] + "…"
    rest = rest if len(rest) <= 80 else "…" + rest[-79:]
    lines = [f"今の成果物 {num}: {name}（{state}）。完成条件: {cond}。残り: {rest}。完成まで新しい成果物を始めない（finish-first）。"]
    if weak:
        lines.append(f"注意: 成果物 {num} の完成条件が、動かして確かめられる文になっていない。コマンド名かテスト名か、出る結果を書く。")
    if due(d, "\n".join(lines)):
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
