#!/usr/bin/env python3
"""Stop フック: GitHub Actions の通知と、確認（❔）の順番を機械で守らせる。

ユーザーの決まり: Actions は必ず通知させ、通知を受け取る。終わっていないのに確認することも、終わっているのに確認しないことも無くす。
3 つを見る。どれかに当たれば止め、足りない 1 行だけを求める（前の行を出し直さない）。
  1. このターンで PR を作ったのに、subscribe_pr_activity を呼んでいない
  2. マージの ❔ を出しているのに、その PR の Actions の完了の通知がまだ無い（か、通知のあとに push している）
  3. Actions の完了か失敗の通知を受けたターンなのに、結果（通った・落ちた）を ❕ で書いていない

環境変数:
  ACTIONS_WATCH_OFF=1   止める
❔ の行に「Actions は無い」と書けば、2 は通る（workflow の無いリポジトリ）。
"""

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import ci_state  # noqa: E402

# マージを頼む ❔ だけ見る。「マージの ❔ はあとで出す」のように触れるだけの行は見ない。行の終わりまで取る（「Actions は無い」が後ろに来る）
MERGE_Q = re.compile(r"^[❔?][^\n]*(マージ(して|し、|する|を|可)|merge (it|this|the pr)|マージしてよい)[^\n]*", re.M | re.I)
NO_ACTIONS = re.compile(r"Actions\s*(は|が)\s*無い|Actions\s*(は|が)\s*ない")
RESULT = re.compile(r"[❕!★].*(Actions|チェック|check).*(通った|通りました|落ちた|落ちました|失敗|成功|green|red)", re.I)


def assistant_texts_after(path: str, start_line: int):
    texts, tools = [], []
    with open(path, encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i <= start_line or '"assistant"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "assistant":
                continue
            for x in e.get("message", {}).get("content", []) or []:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    tools.append(str(x.get("name", "")))
            t = ci_state.text_of(e.get("message", {}).get("content", []))
            if t.strip():
                texts.append(t)
    return texts, tools


def main() -> int:
    if os.environ.get("ACTIONS_WATCH_OFF") == "1":
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
        st = ci_state.read(path)
    except OSError:
        return 0
    turn = ci_state.current_turn(st)
    texts, tools = assistant_texts_after(path, turn["line"])
    mine = "\n".join(texts)
    reasons = []
    # 1. 作ったのに購読していない
    # 止められた作成（フックの差し戻し、エラー）は PR が無いので数えない。成功した作成だけ見る（ci_state.read の acts）
    created = any("create_pull_request" in a["tool"] and a["line"] > turn["line"] for a in st["acts"])
    if created and not any("subscribe_pr_activity" in t for t in tools):
        reasons.append("PR を作ったのに Actions の通知を購読していない。subscribe_pr_activity を呼び、完了の通知が来るまでマージの ❔ を出さない（actions-watch）")
    # 2. 終わっていないのに、マージの確認を出している
    for m in MERGE_Q.finditer(mine):
        line = m.group(0)
        if NO_ACTIONS.search(line):
            continue
        nums = re.findall(r"#(\d+)", line)
        if not nums:
            reasons.append("マージの ❔ に PR の番号が無い。PR を作る確認とマージの確認を分け、マージの ❔ は Actions の結果（通った・落ちた）と PR の番号を添えて出す（actions-watch）")
            break
        for n in nums:
            last = ci_state.latest(st, int(n))
            if not last or last["status"] != "通った" or last.get("stale"):
                why = "通知がまだ無い" if not last else ("通知のあとに push している" if last.get("stale") else f"最後の通知が「{last['status']}」")
                reasons.append(f"PR #{n} の Actions が終わっていないのにマージの ❔ を出している（{why}）。完了の通知を待ち、結果を添えて ❔ を出す。Actions の無いリポジトリなら ❔ に「Actions は無い」と書く（actions-watch）")
                break
        break
    # 3. 終わっているのに、結果を書いていない
    turn_events = [e for e in st["events"] if e["line"] >= turn["line"] and e["status"] in ("通った", "落ちた")]
    if turn_events and not RESULT.search(mine):
        e = turn_events[-1]
        reasons.append(f"Actions の通知（{e['pr']} {e['sha'][:7]} {e['status']}）を受けたのに結果を書いていない。❕ Actions: 通った・落ちた を 1 行書き、通ったらマージの ❔、落ちたら ci-triage（actions-watch）")
    if not reasons:
        return 0
    print(reasons[0], file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
