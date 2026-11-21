#!/usr/bin/env python3
"""UserPromptSubmit フック: 独立した指示が 2 つ以上あるときに、子のセッションに分ける計画（fanout.py plan）を促す。

分ける合図が人の言葉（「並列で」）待ちだと、子のセッションは使われず、1 本で順に進めて開発が遅いままになる。
指示の数と語から機械で合図を出す。差し込むのは 1 行で、同じセッションでは 15 分に 1 回。
親から分けられた子のセッション（プロンプトに「セッションやルーティンを作らない」がある）には出さない。
セッションを増やさない、子のセッションを立てない、とユーザーが決めている会話と、順にやる決まりのルーティン（long-dev）にも出さない。
long-dev の紐づけ先はユーザーが使っているセッションで、子を立てて開発を移すと、そのセッションでは何も起きなくなる（scheduler の「紐づけ先」）。

環境変数:
  FANOUT_OFF=1         止める
  FANOUT_NUDGE_MIN=N   同じセッションで次に出すまでの分数（既定 15）
  FANOUT_STATE_DIR     覚える場所（既定 ~/.claude/state/fanout）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

WORDS = re.compile(r"並列|並行|同時に|それぞれ|手分け|(?:全部の|全|各|複数の)リポジトリ")
ITEM = re.compile(r"(?m)^\s*(?:\d+\s*[.)．、:：]|[①-⑳・]|[-*+]\s)\s*\S")
REQUEST = re.compile(r"(?:して|しろ|やれ|くれ|ください|ように|すること|する|直す|作る|改善|切り替える|入れる|足す|消す|せ|け|て|(?<!の)で)$")
SYSTEMISH = re.compile(r"<task-notification>|^\s*Stop hook feedback|<wake reason=")
CHILD = re.compile(r"セッションやルーティンを作らない|親のセッションから分けた 1 本")
# ユーザーがセッションを増やさないと決めた言い方。あとの発言で「並列で」と言い直されたら、新しい方に従う
NO_SPLIT = re.compile(r"セッションを(?:勝手に)?(?:増やさない|増やすな|立てない|立てるな|作らない|作るな)|子のセッションを(?:立てない|立てるな|作らない|作るな|増やさない)"
                      r"|このセッション(?:だけ)?で(?:動かす|回す|進める)")
SPLIT_AGAIN = re.compile(r"並列|並行|手分け|子のセッションに分け")
# 番号の付いた手順を順にやるルーティン（long-dev の 報告・まとめ・開発）。独立した指示ではない
SEQUENTIAL = re.compile(r"次を順に|順にやる|の long-dev。")
NOTIFY = "ReadNotifications"  # ルーティンのプロンプトは、このツールの結果として届くことがある
SUMMARY = "This session is being continued"  # 文脈を圧縮したときの要約の出だし
LONG = 8000


def _texts(content, notify_ids=()):
    """発言の文と、通知を読むツール（ReadNotifications）の結果の文。ほかのツールの結果は見ない（差分や検索の出力に同じ語が出る）。"""
    if isinstance(content, str):
        return [content]
    out = []
    for x in content or []:
        if not isinstance(x, dict):
            continue
        if x.get("type") == "text":
            out.append(str(x.get("text", "")))
        elif x.get("type") == "tool_result":
            inner = x.get("content")
            t = inner if isinstance(inner, str) else "\n".join(str(y.get("text", "")) for y in inner or [] if isinstance(y, dict))
            if x.get("tool_use_id") in notify_ids:
                out.append(t)
    return out


def user_forbids(prompt: str, transcript_path: str = "") -> bool:
    """ユーザーがセッションを増やさないと言っているか。会話の中の最後の言い方に従う（増やさない／並列で）。"""
    state = False
    notify_ids = set()
    if transcript_path and os.path.isfile(transcript_path):
        try:
            with open(transcript_path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    if NOTIFY in line and '"tool_use"' in line:
                        try:
                            e = json.loads(line)
                        except ValueError:
                            continue
                        c = e.get("message", {}).get("content", []) if e.get("type") == "assistant" else []
                        notify_ids.update(x.get("id") for x in c if isinstance(x, dict) and x.get("type") == "tool_use" and str(x.get("name", "")).endswith(NOTIFY))
                        continue
                    if '"user"' not in line or not ("セッション" in line or "並列" in line or "並行" in line or "手分け" in line):
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("type") != "user":
                        continue
                    for t in _texts(e.get("message", {}).get("content", []), notify_ids):
                        # 圧縮の要約と長い貼り付けは、ほかの場面の言葉を引いていることがあるので見ない（元の発言は記録に残っている）
                        if SYSTEMISH.search(t) or CHILD.search(t) or len(t) > LONG or t.lstrip().startswith(SUMMARY):
                            continue
                        if NO_SPLIT.search(t):
                            state = True
                        elif SPLIT_AGAIN.search(t):
                            state = False
        except OSError:
            pass
    if NO_SPLIT.search(prompt):
        return True
    if SPLIT_AGAIN.search(prompt):
        return False
    return state


def count_instructions(prompt: str) -> int:
    """独立した指示の数。番号や「・」の列があればその数、無ければ頼む形で終わる文の数。"""
    items = len(ITEM.findall(prompt))
    if items >= 2:
        return items
    sentences = [s.strip(" 　、") for s in re.split(r"[。\n！!]", prompt)]
    return sum(1 for s in sentences if len(s) >= 6 and REQUEST.search(s))


def due(session_id: str, minutes: int) -> bool:
    """このセッションで、前に出してから minutes 分たっていれば True にして、今の時刻を覚える。"""
    root = Path(os.environ.get("FANOUT_STATE_DIR") or Path.home() / ".claude" / "state" / "fanout")
    f = root / "nudge.json"
    key = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:60] or "none"
    try:
        seen = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    now = time.time()
    if now - float(seen.get(key, 0)) < minutes * 60:
        return False
    seen = {k: v for k, v in seen.items() if now - float(v) < 86400}  # 古いセッションの行を残さない
    seen[key] = now
    try:
        root.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(seen), encoding="utf-8")
    except OSError:
        pass
    return True


def main() -> int:
    if os.environ.get("FANOUT_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = str(d.get("prompt") or "")
    if not prompt.strip() or SYSTEMISH.search(prompt) or CHILD.search(prompt):
        return 0
    n = count_instructions(prompt)
    word = WORDS.search(prompt)
    if SEQUENTIAL.search(prompt):
        n = 0  # 順にやる手順の番号は、独立した指示の数に入れない
    if n < 2 and not word:
        return 0
    if user_forbids(prompt, str(d.get("transcript_path") or "")):
        return 0
    try:
        minutes = max(0, int(os.environ.get("FANOUT_NUDGE_MIN", "15")))
    except ValueError:
        minutes = 15
    if not due(str(d.get("session_id") or ""), minutes):
        return 0
    why = f"独立した指示が {n} 個ある" if n >= 2 else f"「{word.group(0)}」とある"
    print(f"{why}。10 手以上の作業が 2 つ以上なら、子のセッションに分けて並列で進める（fanout.py plan で束・難易度に合うモデル・プロンプトを出す。feedback-session）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
