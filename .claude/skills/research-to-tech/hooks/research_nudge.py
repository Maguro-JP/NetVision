#!/usr/bin/env python3
"""PostToolUse フック（WebSearch、WebFetch、Context7、Exa）: 調べ物をした直後に、情報で終わらせない手順を 1 行差し込む。

調べている最中に知らせる方が、あとで思い出して書くより抜けない。同じ文を何度も出すとチャットが流れるので、
同じセッションでは 15 分に 1 回だけ出す。止めはしない（止めるのは Stop フックの research_gate.py）。

環境変数:
  RESEARCH_TO_TECH_OFF=1     止める
  RESEARCH_TO_TECH_STATE=dir 前に出した時刻の置き場（既定は ~/.claude/state/research-to-tech）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

RESEARCH = re.compile(r"^(WebSearch|WebFetch)$|^mcp__(Context7|Exa)__")
EVERY = 15 * 60
MSG = ("調べ物をした。情報のまま終えない。python3 .claude/skills/research-to-tech/scripts/knowledge.py --find <語> で既にある知識を見て、"
       "無ければ --add で残し、知識 → アルゴリズム → 技術（コードとテスト）→ 実装まで進める（research-to-tech）")


def main() -> int:
    if os.environ.get("RESEARCH_TO_TECH_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if not RESEARCH.search(str(d.get("tool_name", ""))):
        return 0
    base = os.environ.get("RESEARCH_TO_TECH_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "research-to-tech"
    sid = re.sub(r"[^A-Za-z0-9_-]", "", str(d.get("session_id", "")))[:40] or "session"
    stamp = root / f"{sid}.last"
    now = time.time()
    try:
        if stamp.is_file() and now - float(stamp.read_text().strip() or 0) < EVERY:
            return 0
        root.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(now))
    except (OSError, ValueError):
        pass
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": MSG}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
