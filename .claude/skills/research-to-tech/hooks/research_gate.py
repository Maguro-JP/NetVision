#!/usr/bin/env python3
"""Stop フック: 開発で調べ物をしたのに、知識に残さずに終えるのを止める。

ユーザーの決まり: 調べ物を、ただの情報で終わらせない。知識として蓄え、アルゴリズムを作り、技術にして実装する。
見るのは 3 つがそろったとき。
  1. この 3 ターン以内に調べ物（WebSearch、WebFetch、Context7、Exa）をしている
  2. その調べ物のあとに、docs/knowledge/ への書き込み（knowledge.py --add を含む）が無い
  3. このターンで開発をしている（docs/knowledge/ 以外への Edit・Write か、git commit）
質問に答えるだけの調べ物（開発をしていないターン）は止めない。残すほどでない調べ物は、
「❕ 調べ物: 知識にしない（理由）」を 1 行書けば通る。

環境変数:
  RESEARCH_TO_TECH_OFF=1   止める
"""

import json
import os
import re
import sys

RESEARCH = re.compile(r"^(WebSearch|WebFetch)$|^mcp__(Context7|Exa)__")
EDIT = ("Edit", "Write", "MultiEdit", "NotebookEdit")
ESCAPE = re.compile(r"^[❕!]\s*調べ物[:：][^\n]*(知識にしない|残さない)", re.M)
WINDOW = 3  # 何ターン前の調べ物まで見るか


def text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(str(x.get("text", "")) for x in content or [] if isinstance(x, dict) and x.get("type") == "text")


def is_real_user(content) -> bool:
    """人の発言か。ツールの結果、フックの差し戻し、裏の処理の通知は同じターンの続きとして扱う。"""
    if not isinstance(content, str) and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content or []):
        return False
    t = text_of(content).lstrip()
    return not (t.startswith("Stop hook feedback") or "<task-notification>" in t[:600])


def knowledge_write(name: str, inp: dict) -> bool:
    path = str(inp.get("file_path", ""))
    cmd = str(inp.get("command", ""))
    if name in EDIT and "docs/knowledge/" in path:
        return True
    if name == "Bash" and "knowledge.py" in cmd and "--add" in cmd:
        return True
    return name == "Bash" and "docs/knowledge/" in cmd and bool(re.search(r">|\btee\b|write_text|<<", cmd))


def dev_edit(name: str, inp: dict) -> bool:
    if name in EDIT:
        return "docs/knowledge/" not in str(inp.get("file_path", ""))
    return name == "Bash" and bool(re.search(r"\bgit\s+(-\S+\s+\S+\s+)*commit\b", str(inp.get("command", ""))))


def scan(path: str) -> dict:
    turn = 0
    research, writes, edits, texts = [], [], [], {}
    pos = 0
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"user"' not in line and '"assistant"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            c = e.get("message", {}).get("content", [])
            if e.get("type") == "user":
                if is_real_user(c):
                    turn += 1
                continue
            if e.get("type") != "assistant":
                continue
            pos += 1
            t = text_of(c)
            if t.strip():
                texts.setdefault(turn, []).append(t)
            for x in c if not isinstance(c, str) else []:
                if not (isinstance(x, dict) and x.get("type") == "tool_use"):
                    continue
                name, inp = str(x.get("name", "")), x.get("input") or {}
                if RESEARCH.search(name):
                    research.append((turn, pos))
                elif knowledge_write(name, inp):
                    writes.append((turn, pos))
                elif dev_edit(name, inp):
                    edits.append((turn, pos))
    return {"turn": turn, "research": research, "writes": writes, "edits": edits, "texts": texts}


def pending(st: dict) -> int:
    """知識に残していない調べ物の回数（この 3 ターン以内で、最後の書き込みより後）。"""
    last_write = max((p for _, p in st["writes"]), default=-1)
    return sum(1 for t, p in st["research"] if t > st["turn"] - WINDOW and p > last_write)


def main() -> int:
    if os.environ.get("RESEARCH_TO_TECH_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if d.get("stop_hook_active"):
        return 0
    path = d.get("transcript_path")
    if not path or not os.path.isfile(path):
        return 0
    try:
        st = scan(path)
    except OSError:
        return 0
    n = pending(st)
    if not n or not any(t == st["turn"] for t, _ in st["edits"]):
        return 0
    if ESCAPE.search("\n".join(st["texts"].get(st["turn"], []))):
        return 0
    print(f"調べ物（{n} 回）を知識に残していない。knowledge.py --add で docs/knowledge/tech/ に 1 件作り、情報・知識・アルゴリズム・技術・実装を埋める。"
          "残すほどでなければ「❕ 調べ物: 知識にしない（理由）」を 1 行書く（research-to-tech）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
