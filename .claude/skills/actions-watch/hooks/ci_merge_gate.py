#!/usr/bin/env python3
"""PreToolUse フック（merge_pull_request）: Actions の完了の通知が無い PR をマージしない。

pr_gate.py が「❔ への返事」を見るのに対し、ここは「その PR の Actions が終わって通ったか」を会話の記録の通知で見る。
終わっていない PR、通知のあとに push した PR、落ちた PR のマージを止める。Actions の無いリポジトリは、❔ の行に
「Actions は無い」と書いてあれば通す。

環境変数:
  ACTIONS_WATCH_OFF=1   止める
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ci_state  # noqa: E402


def main() -> int:
    if os.environ.get("ACTIONS_WATCH_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    if "merge_pull_request" not in str(d.get("tool_name", "")):
        return 0
    ti = d.get("tool_input") or {}
    n = ti.get("pullNumber") or ti.get("pull_number")
    repo = f"{ti.get('owner', '')}/{ti.get('repo', '')}"
    path = d.get("transcript_path")
    if not n or not path or not os.path.isfile(path):
        return 0
    try:
        st = ci_state.read(path)
        raw = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return 0
    if re.search(rf"[❔?][^\n]*#{int(n)}\b[^\n]*Actions\s*(は|が)\s*(無い|ない)", raw):
        return 0
    last = ci_state.latest(st, int(n), repo)
    if last and last["status"] == "通った" and not last.get("stale"):
        return 0
    why = "通知がまだ無い" if not last else ("通知のあとに push している" if last.get("stale") else f"最後の通知が「{last['status']}」")
    print(f"PR #{n} の Actions が終わって通った通知が無い（{why}）。subscribe_pr_activity で購読し、完了の通知を待ってからマージする。Actions の無いリポジトリなら ❔ の行に「Actions は無い」と書く（actions-watch）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
