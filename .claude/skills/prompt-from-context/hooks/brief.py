#!/usr/bin/env python3
"""UserPromptSubmit フック: 短い一言のとき、前の文脈を差し込み、全文のプロンプトを組み立ててから動かせる。

「やれ」「それも」「直して」のような一言は、対象も形も前の会話にある。会話が長いと、直前に出した
成果物や ❔ が文脈の後ろに埋もれ、一言を文字どおりにだけ読んで聞き返したり、狭く作ったりする。
そこで一言が短いときだけ、transcript から直前のユーザーの発言と自分の ★ ↩ ❕ ❔ の行を拾って差し込む。
長い一言のときは何も出さない。毎ターンの差し込みを増やさないため。
短い一言の数は metrics.jsonl に残し、週の報告（skill-upkeep）が読む。

環境変数:
  PROMPT_FROM_CONTEXT_OFF=1      止める
  PROMPT_FROM_CONTEXT_STATE=dir  数の保持先（既定は ~/.claude/state/prompt-from-context）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

SHORT = 40      # これ以下の長さは短い一言
WITH_HINT = 120  # 指示語があれば、この長さまで短い一言として扱う
KEEP_USER = 2   # 差し込む前の一言の数
KEEP_MARK = 3   # 差し込む自分の行の数
CUT = 90        # 1行の長さ
# 対象を前の会話に頼る語。「それ」「同じ」「も」「続けて」「やれ」など
HINT = re.compile(
    r"それ|これ|あれ|そっち|こっち|さっき|前の|同じ|全部|続き|続けて|やり直|戻せ|戻して"
    r"|(?:も|を)?(?:やれ|やって|やろ|直して|直せ|進めて|入れて|消して|出して|見て|お願い|頼む)$"
)
SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|system-reminder|hook (?:success|additional)|This session is being continued|Summary:")
REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.S)
MARKS = "★⭐↩⏎❕❗❔❓"


def is_short(prompt: str) -> bool:
    if not prompt or prompt.startswith("/") or SYSTEMISH.search(prompt):
        return False
    n = len(prompt)
    return n <= SHORT or (n <= WITH_HINT and bool(HINT.search(prompt)))


def text_of(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")


def recent(path: str, current: str):
    """(前のユーザーの一言, 自分の ★↩❕❔ の行, 答え待ちの ❔)。ツールの結果と通知は数えない。"""
    users, marks, pending = [], [], ""
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if not isinstance(c, str) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                continue
            t = re.sub(r"\s+", " ", REMINDER.sub("", text_of(c))).strip()
            if t and not SYSTEMISH.search(t):
                users.append(t)
                pending = ""
        elif e.get("type") == "assistant":
            for l in text_of(c).splitlines():
                l = l.strip()
                if l and l[0] in MARKS:
                    marks.append(l)
                    if l[0] in "❔❓":
                        pending = l
    if users and users[-1] == current:
        users.pop()
    return users[-KEEP_USER:], marks[-KEEP_MARK:], pending


def record(session_id: str, row: dict) -> None:
    base = os.environ.get("PROMPT_FROM_CONTEXT_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "prompt-from-context"
    try:
        root.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "session": re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:40], **row}
        with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def cut(s: str) -> str:
    return s if len(s) <= CUT else s[: CUT - 1] + "…"


def main() -> int:
    if os.environ.get("PROMPT_FROM_CONTEXT_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = re.sub(r"\s+", " ", (d.get("prompt") or "")).strip()
    if not is_short(prompt):
        return 0
    users, marks, pending = [], [], ""
    path = d.get("transcript_path")
    if path and os.path.isfile(path):
        try:
            users, marks, pending = recent(path, prompt)
        except OSError:
            pass
    record(d.get("session_id", ""), {"len": len(prompt), "context_lines": len(users) + len(marks), "pending": bool(pending)})
    out = [
        f"短い一言（{len(prompt)} 文字）。前の文脈から対象・やること・形・完成条件を補い、「↩ 読み取り: …と読みました」を 1 行出してから動く（prompt-from-context）。聞き返さない。"
    ]
    if users or marks:
        out.append("直前の文脈:")
        out += [f"- 前の一言: {cut(u)}" for u in users]
        out += [f"- 自分の行: {cut(m)}" for m in marks]
    if pending:
        out.append(f"- 答え待ちの ❔: {cut(pending)}（「やれ」「はい」「それで」はこれへの答え）")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
