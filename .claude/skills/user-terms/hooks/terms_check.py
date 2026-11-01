#!/usr/bin/env python3
"""Stop フック: 止まる直前の返答に、ユーザーの語と違う言い方（略語・訳語・造語）があれば差し戻す。

ユーザーは自分の語で読む。私が「プルリク」「リポ」「Routine」「片付け」と書くと、読む側が言い換える手間を負う。
指示だけでは直らなかったので、止まる直前に対応表（references/user-terms.md）と機械で突き合わせる。
コード、引用、URL、パスの中は文章ではないので見ない（scripts/terms.py）。
見つけた数は metrics.jsonl に残し、週の報告（skill-upkeep）が読む。

環境変数:
  USER_TERMS_OFF=1        止める
  USER_TERMS_STATE=dir    数の保持先（既定は ~/.claude/state/user-terms）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from terms import TABLE, find, load_table  # noqa: E402


def text_of(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")


def this_turn_texts(path: str):
    """最後の人の発言から後の、自分の文。ツールの結果は人の発言に数えない。"""
    texts = []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if isinstance(c, str) or not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                texts = []
            continue
        if e.get("type") == "assistant":
            t = text_of(c)
            if t.strip():
                texts.append(t)
    return texts


def record(session_id: str, row: dict) -> None:
    root = Path(os.environ.get("USER_TERMS_STATE") or Path.home() / ".claude" / "state" / "user-terms")
    try:
        root.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "session": re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:40], **row}
        with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("USER_TERMS_OFF") == "1":
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
        texts = this_turn_texts(path)
    except OSError:
        return 0
    if not texts:
        return 0
    hits = find("\n".join(texts), load_table())
    pairs = []
    for _, v, u in hits:
        if (v, u) not in pairs:
            pairs.append((v, u))
    record(d.get("session_id", ""), {"hits": len(hits), "words": [v for v, _ in pairs][:20]})
    if not pairs:
        return 0
    shown = "、".join(f"「{v}」→「{u}」" for v, u in pairs[:8])
    more = f" ほか {len(pairs) - 8} 個" if len(pairs) > 8 else ""
    print(f"ユーザーの語と違う言い方が {len(pairs)} 個ある: {shown}{more}。その行だけをユーザーの語で直し、前の行を出し直さない"
          f"（user-terms。表は {TABLE.relative_to(Path.cwd()) if TABLE.is_relative_to(Path.cwd()) else TABLE}）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
