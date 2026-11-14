#!/usr/bin/env python3
"""UserPromptSubmit フック: Actions の通知が届いたターンに、何をするかを 1 行差し込む。

通知を受け取っても読まない・結果を書かない、を無くすため。溜まっている通知があれば先に ReadNotifications を呼ばせ、
完了の通知なら結果を ❕ で書かせ、通ったらマージの ❔、落ちたら ci-triage に進ませる。他の通知（bot のコメント）には何も出さない。

環境変数:
  ACTIONS_WATCH_OFF=1   止める
"""

import json
import os
import re
import sys


def main() -> int:
    if os.environ.get("ACTIONS_WATCH_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    p = str(d.get("prompt", ""))
    msg = ""
    if "queued-remote-notifications" in p:
        msg = "通知が溜まっている。先に ReadNotifications を呼ぶ。Actions の完了（check_suite）があれば ❕ で結果を 1 行、通ったらマージの ❔（PR の番号と結果を添える）、落ちたら ci-triage（actions-watch）"
    elif re.search(r"kind=\"?(check_suite\.completed|workflow_run\.completed|check_run\.completed)", p):
        failed = bool(re.search(r"\"conclusion\":\s*\"(failure|timed_out|action_required|startup_failure)\"", p))
        msg = ("Actions が落ちた通知。❕ で結果を 1 行書き、ci-triage で原因を特定して直す。マージの ❔ は出さない（actions-watch）" if failed else
               "Actions の完了の通知。❕ Actions: 通った を 1 行書き、マージの確認が要るなら PR の番号と結果を添えて ❔ を出す。他の返事はしない（actions-watch）")
    elif re.search(r"kind=\"?(check_suite\.(requested|rerequested)|workflow_run\.(requested|in_progress))", p):
        msg = "Actions が動き始めた通知。待つ。sleep も polling もしない。完了の通知で ❕ と ❔ を出す（actions-watch）"
    if not msg:
        return 0
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
