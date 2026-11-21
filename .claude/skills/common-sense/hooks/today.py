#!/usr/bin/env python3
"""SessionStart と UserPromptSubmit のフック: 今日の日付（日本時間）と「学習の知識は古い」を 1 行で差し込む。

AI の学習の知識は数年前で止まっている。その自覚が無いと、版・API・フラグ・モデルの名前・値段を記憶で書き、
「最新」を学習の時点の最新のまま答える。今日の日付を見せると、記憶との差に気づける。

SessionStart では毎回出す。UserPromptSubmit では 10 ターンに 1 回と、日付が変わったときだけ出す
（毎ターン同じ文を出すとチャットが流れ、1 ターンの差し込みの目安を使う）。通知のターンには出さない。

環境変数:
  COMMON_SENSE_OFF=1        止める
  COMMON_SENSE_EVERY=N      UserPromptSubmit で出し直す間隔（既定 10 ターン）
  COMMON_SENSE_STATE=dir    ターンの数と前に出した日付の置き場
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cs  # noqa: E402

LIMIT = 120
SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|This session is being continued")


def line(day: str) -> str:
    return f"今日は {day}（日本時間）。学習の知識は古い。外の事実（版・API・フラグ・モデルの名前・値段）は一次情報で確かめてから書く（common-sense）"


def due(d: dict, day: str) -> bool:
    """今出すか。SessionStart は出す。UserPromptSubmit は、N ターンごとと、日付が変わったときだけ。"""
    ev = str(d.get("hook_event_name") or "")
    is_prompt = ev == "UserPromptSubmit" if ev else "prompt" in d
    sid = d.get("session_id")
    if not sid:
        return True  # セッションが分からなければ、間引かない
    st = _cs.load(sid, "today")
    turn = int(st.get("turn", 0)) + (1 if is_prompt else 0)
    try:
        every = max(1, int(os.environ.get("COMMON_SENSE_EVERY") or 10))
    except ValueError:
        every = 10
    show = (not is_prompt) or turn % every == 0 or (bool(st.get("day")) and st.get("day") != day)
    _cs.save(sid, "today", {"turn": turn, "day": day})
    return show


def main() -> int:
    if _cs.off():
        return 0
    d = _cs.read_event()
    if SYSTEMISH.search(str(d.get("prompt") or "")):
        return 0  # 通知のターンには出さない
    day = _cs.today()
    if due(d, day):
        print(line(day)[:LIMIT])
    return 0


if __name__ == "__main__":
    sys.exit(main())
