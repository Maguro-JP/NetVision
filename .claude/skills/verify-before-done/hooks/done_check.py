#!/usr/bin/env python3
"""Stop フック: 「完了」と書いた返答に根拠が無ければ差し戻す。

「直しました」「通りました」と書いただけで、動かした出力も場所も無い返答が、嘘と同じ結果を生む。
指示だけでは直らなかったので、止まる直前に機械で見る。
根拠と数えるのは、URL、コミットのハッシュ、バッククォートの中身、コードブロック、ファイルの場所、
PR 番号、`exit=` の行。「確かめていない」と書いてあれば、それも通す（正直な方が良い）。
❔ が 1 ターンに 2 つ以上あれば、それも差し戻す。質問を並べると人が決める負担になる。
訂正の合図がある発言への返事に、反論の形（しかし、ただし、おすすめしません、の方が良い）があれば差し戻す。反抗の禁止。
返答ごとの数は metrics.jsonl に残し、週の報告（skill-upkeep）が読む。

環境変数:
  DONE_CHECK_OFF=1        止める
  DONE_CHECK_STATE=dir    数の保持先（既定は ~/.claude/state/verify-before-done）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

CLAIM = re.compile(
    r"完了|完成しました|できました|出来ました|終わりました|終えました|直しました|直りました|通りました|通っています"
    r"|マージしました|入りました|入っています|入れました|動きました|動いています|作りました|足しました|消しました"
    r"|確かめました|確認しました|検証しました|テストが通|反映しました|登録しました|設定しました|デプロイしました"
)
EVIDENCE = re.compile(
    r"https?://|\b[0-9a-f]{7,40}\b|`[^`\n]+`|^```|exit=|#\d+|確かめていない|確認していない|未確認|未検証"
    r"|[\w./-]+\.(?:md|py|sh|json|ya?ml|ts|tsx|js|go|rs|java|rb|toml|txt|csv)\b|[\w./-]+:\d+",
    re.M,
)
MARKS = "★⭐↩⏎❕❗❔❓"
# 訂正の合図（follow-instructions の directives.py と同じ見方）と、訂正への返事に出てはいけない反論の形
CORRECTION = re.compile(
    r"違う|ちがう|やめて|やめろ|勝手に|言った(?:通り|とおり|のに|だろ|でしょ|はず)|言ってる|言ってない"
    r"|聞いて(?:ない|いない|た\?|たか)|聞け|聞かない|それじゃない|そうじゃない|じゃなくて|ではなくて"
    r"|戻して|元に戻|しないで|するな|使うな|やるな|書くな|禁止|またか|また同じ|何度も|何回|何度"
    r"|反抗|従わ|改めない|貫く|頼んでない|余計な|指示通り|指示どおり|従え"
)
PUSHBACK = re.compile(r"しかし|ただし|とはいえ|おすすめしません|お勧めしません|の方が良い|のほうが良い|べきではない|やめた方が|やめたほうが|反対です|賛成できません|AI の判断で|私の判断で")


def response_texts(path: str):
    """この返答（最後の人の発言のあと）に書いた文と、その人の発言。ツールの結果は区切りにしない。"""
    texts = []
    user = ""
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if isinstance(c, str) or not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                texts = []
                user = c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            continue
        if e.get("type") != "assistant":
            continue
        t = c if isinstance(c, str) else "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        if t.strip():
            texts.append(t)
    return texts, user


def record(session_id: str, row: dict) -> None:
    base = os.environ.get("DONE_CHECK_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "verify-before-done"
    try:
        root.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "session": re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:40], **row}
        with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("DONE_CHECK_OFF") == "1":
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
        texts, user = response_texts(path)
    except OSError:
        return 0
    if not texts:
        return 0
    text = "\n".join(texts)
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    questions = sum(1 for l in lines if l[0] in "❔❓")
    outside = sum(1 for l in lines if l[0] not in MARKS)
    claim = bool(CLAIM.search(text))
    evidence = bool(EVIDENCE.search(text))
    bare = claim and not evidence
    corrected = bool(CORRECTION.search(user or ""))
    # 引用（「…」）とコード（`…`）の中の語は、反論ではなく説明なので外してから見る
    def strip_quotes(l: str) -> str:
        return re.sub(r"「[^」]*」|`[^`]*`", "", l)
    pushback = [l for l in lines if PUSHBACK.search(strip_quotes(l)) and "権限" not in l] if corrected else []
    record(d.get("session_id", ""), {"lines": len(lines), "questions": questions, "outside_marks": outside,
                                     "claim": claim, "claim_without_evidence": bare, "corrected": corrected, "pushback": len(pushback)})
    reasons = []
    if pushback:
        reasons.append(f"訂正への返事に反論の形がある（「{PUSHBACK.search(pushback[0]).group(0)}」）。1文で認めて、言われた通りに直したものだけを出す。"
                       "代案は直したあとに ❔ で1行だけ（follow-instructions。反抗の禁止）。")
    if bare:
        m = CLAIM.search(text)
        reasons.append(f"「{m.group(0)}」と書いているが、根拠（動かした出力、URL、コミットのハッシュ、ファイルの場所）が無い。"
                       "根拠を足すか、確かめていないなら「確かめていない」と書く（verify-before-done）。")
    if questions > 1:
        reasons.append(f"❔ が {questions} 行ある。1 ターンに 1 つまで。残りは決めて進めるか、ファイルに書いて ★ で渡す（follow-instructions）。")
    if reasons:
        print("\n".join(reasons), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
