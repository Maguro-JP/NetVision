#!/usr/bin/env python3
"""UserPromptSubmit フック: ユーザーの発言に出た語を数えて残す。対応表を育てる材料にする。

対応表は私が思いついた語ではなく、ユーザーが実際に使った語から作る。
発言ごとにカタカナ語・英数字の語・漢字の連なりを数え、~/.claude/state/user-terms/terms.json に足す。何も出さない。
週 1 回（skill-upkeep）、--candidates で「2 回以上使われていて表に無い語」を見て、自分が別の言い方をしていれば表に足す。
通知、フックの差し戻し、長い貼り付けは発言ではないので数えない。

使い方:
  terms_learn.py                      フックとして（標準入力の JSON の prompt を数える）
  terms_learn.py --scan <transcript>  transcript の人の発言を全部数える（表の種を作るとき）
  terms_learn.py --candidates [N]     表に無い語を使われた回数の順に N 個（既定 30）出す
環境変数:
  USER_TERMS_STATE=dir    保持先（既定は ~/.claude/state/user-terms）
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from terms import clean_lines, load_table  # noqa: E402

SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|system-reminder|This session is being continued"
                       r"|hook feedback|hook success|<command-name>|Caveat:")
WORD = re.compile(r"[ァ-ヴー]{2,}|[A-Za-z][A-Za-z0-9_-]{1,}|[一-龥]{2,}")
MAX_LEN = 1500
KEEP = 3000


def state_path() -> Path:
    return Path(os.environ.get("USER_TERMS_STATE") or Path.home() / ".claude" / "state" / "user-terms") / "terms.json"


def load_state() -> dict:
    try:
        d = json.loads(state_path().read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("words"), dict):
            return d
    except (OSError, ValueError):
        pass
    return {"n": 0, "words": {}}


def save_state(d: dict) -> None:
    words = dict(sorted(d["words"].items(), key=lambda kv: -kv[1])[:KEEP])
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"n": d["n"], "words": words}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def words_of(prompt: str):
    text = "\n".join(clean_lines(prompt))
    return WORD.findall(text)


def is_prompt(prompt: str) -> bool:
    return bool(prompt.strip()) and not SYSTEMISH.search(prompt) and len(prompt) <= MAX_LEN


def count(d: dict, prompt: str) -> None:
    if not is_prompt(prompt):
        return
    d["n"] += 1
    for w in words_of(prompt):
        d["words"][w] = d["words"].get(w, 0) + 1


def scan(d: dict, path: str) -> None:
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("type") != "user":
            continue
        c = e.get("message", {}).get("content", [])
        if isinstance(c, str):
            t = c
        elif any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
            continue
        else:
            t = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        count(d, re.sub(r"<system-reminder>.*?</system-reminder>", "", t, flags=re.S).strip())


def candidates(d: dict, n: int):
    rows = load_table()
    users = {u for u, _, _ in rows}
    known = {u for u in users} | {w for w in d["words"] for _, _, pat in rows if pat.fullmatch(w)}
    return [(w, c) for w, c in sorted(d["words"].items(), key=lambda kv: -kv[1]) if c >= 2 and w not in known][:n]


def main() -> int:
    a = sys.argv[1:]
    d = load_state()
    if a[:1] == ["--scan"]:
        for p in a[1:]:
            scan(d, p)
        save_state(d)
        print(f"発言 {d['n']} 件、語 {len(d['words'])} 個")
        return 0
    if a[:1] == ["--candidates"]:
        n = int(a[1]) if len(a) > 1 else 30
        rows = candidates(d, n)
        print("表に無い語（使われた回数の順）: " + "、".join(f"{w} {c}" for w, c in rows) if rows else "表に無い語は無い")
        return 0
    try:
        prompt = json.load(sys.stdin).get("prompt", "") or ""
    except ValueError:
        return 0
    count(d, prompt)
    save_state(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
