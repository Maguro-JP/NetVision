#!/usr/bin/env python3
"""Stop フック: 指示がいくつもある長いターンを、分けずに 1 本で進めたまま終えるのを止める。

見るのは 4 つがそろったとき。
  1. このターンのツール呼び出しが 40 回を超えている
  2. このターンを始めた人の発言に、独立した指示が 3 個以上ある
  3. このターンで Agent も create_session も Workflow も使っていない
  4. 返答に「❕ 並列: 分けない（理由）」の行が無い
止めたら、残りを分けるか、分けない理由を 1 行書けば通る。しきい値は推測で置いた値なので、環境変数で変えられる。
親から分けられた子のセッション（プロンプトに「セッションやルーティンを作らない」がある）は見ない。

環境変数:
  FANOUT_OFF=1          止める
  FANOUT_GATE_CALLS=N   ツール呼び出しのしきい値（既定 40）
  FANOUT_GATE_ITEMS=N   指示の数のしきい値（既定 3）
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fanout_nudge import CHILD, SEQUENTIAL, count_instructions, user_forbids  # noqa: E402

ESCAPE = re.compile(r"(?m)^[❕!]\s*並列[:：]")
SPLIT_TOOL = re.compile(r"^(Agent|Task|Workflow)$|create_session$")


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


def scan(path: str) -> dict:
    """最後の人の発言と、そのあとのツール呼び出しの数・分ける道具を使ったか・返答の文。"""
    st = {"prompt": "", "calls": 0, "split": False, "text": ""}
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
                    st = {"prompt": text_of(c), "calls": 0, "split": False, "text": ""}
            elif e.get("type") == "assistant":
                st["text"] += text_of(c) + "\n"
                for x in c if not isinstance(c, str) else []:
                    if isinstance(x, dict) and x.get("type") == "tool_use":
                        st["calls"] += 1
                        st["split"] = st["split"] or bool(SPLIT_TOOL.search(str(x.get("name", ""))))
    return st


def env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def main() -> int:
    if os.environ.get("FANOUT_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    path = d.get("transcript_path")
    if d.get("stop_hook_active") or not path or not os.path.isfile(path):
        return 0
    try:
        st = scan(path)
    except OSError:
        return 0
    if st["split"] or CHILD.search(st["prompt"]) or ESCAPE.search(st["text"]):
        return 0
    # 順にやるルーティン（long-dev）と、ユーザーがセッションを増やさないと言った会話では、分けることを求めない
    if SEQUENTIAL.search(st["prompt"]) or user_forbids(st["prompt"], path):
        return 0
    n = count_instructions(st["prompt"])
    if st["calls"] <= env_int("FANOUT_GATE_CALLS", 40) or n < env_int("FANOUT_GATE_ITEMS", 3):
        return 0
    print(f"指示が {n} 個あるのに、ツール呼び出し {st['calls']} 回を 1 本で進めている。残りを fanout.py plan で子のセッションに分けるか、"
          "「❕ 並列: 分けない（理由）」を 1 行書く（feedback-session）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
