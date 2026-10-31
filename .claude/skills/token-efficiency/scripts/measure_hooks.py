#!/usr/bin/env python3
"""settings.json に登録されたフックを無害な入力で動かし、1 ターンに差し込む文字数を測る。

毎ターン同じ決まりを差し込むと、それ自体がトークンの無駄になる。目安は UserPromptSubmit の合計 1,200 文字、
SessionStart の合計 5,000 文字。超えたら、出す頻度を落とすか、短くする。

使い方: measure_hooks.py [settings.json] [--budget 1200] [--start-budget 5000]
目安を超えると exit 1。
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


STATE = tempfile.mkdtemp()


def run(cmd: str, payload: dict) -> int:
    """2 回動かして 2 回目の長さを返す。1 ターン目だけ全文を出すフックがあるので、普段のターンの量を測る。"""
    n = 0
    for _ in range(2):
        try:
            r = subprocess.run(cmd, shell=True, input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True, timeout=60,
                               env=dict(os.environ, LANG_RULE_STATE=STATE, TOKEN_EFFICIENCY_STATE=STATE, FOLLOW_INSTRUCTIONS_STATE=STATE))
            n = len(r.stdout)
        except (OSError, subprocess.TimeoutExpired):
            return 0
    return n


def main() -> int:
    args = sys.argv[1:]
    budget = int(args[args.index("--budget") + 1]) if "--budget" in args else 1200
    start_budget = int(args[args.index("--start-budget") + 1]) if "--start-budget" in args else 5000
    settings = next((a for a in args if a.endswith(".json")), ".claude/settings.json")
    d = json.load(open(settings, encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp()) / "t.jsonl"
    tmp.write_text(json.dumps({"type": "user", "message": {"content": "README の表を直して"}}, ensure_ascii=False) + "\n", encoding="utf-8")
    over = False
    for ev, limit in (("SessionStart", start_budget), ("UserPromptSubmit", budget)):
        total = 0
        for row in d.get("hooks", {}).get(ev, []):
            for h in row.get("hooks", []):
                payload = {"session_id": "measure-" + Path(STATE).name, "transcript_path": str(tmp), "cwd": os.getcwd(), "hook_event_name": ev,
                           "prompt": "README の表を直して", "source": "startup"}
                n = run(h["command"], payload)
                total += n
                print(f"  {ev:17} {n:6d}  {h['command'][:72]}")
        mark = "超過" if total > limit else "目安内"
        over = over or total > limit
        print(f"{ev} 合計 {total} 文字（目安 {limit}）: {mark}")
    return 1 if over else 0


if __name__ == "__main__":
    sys.exit(main())
