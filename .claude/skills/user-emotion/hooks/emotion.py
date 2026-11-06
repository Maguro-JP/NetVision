#!/usr/bin/env python3
"""UserPromptSubmit フック: ユーザーの一言から感情を読み、どう動くかを 1 行で差し込む。

ユーザーがいらだっているときに、謝る・言い訳する・聞き返す・長く書く、は火に油。同じ指示を 2 回言わせるのは、1 回目をやらなかった印。
機械で手がかりを拾い、感情ごとの動き方（謝らない、聞き返さない、1〜2 行、今すぐやる）を差し込む。感情を言い当てて返さない（「お怒りですね」は邪魔）。
感情は ~/.claude/state/user-emotion/metrics.jsonl に書き、週 1 回の skill-upkeep が「いらだちの回数」として数える。

環境変数:
  USER_EMOTION_OFF=1       止める
  USER_EMOTION_STATE=dir   記録の保持先（既定は ~/.claude/state/user-emotion）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION|system-reminder|Stop hook feedback|This session is being continued")
# 強い順。先に当たったものを感情とする
CUES = [
    ("怒り", re.compile(r"無能|クソ|ふざけ|ゴミ|最悪|ありえない|信用できない|バカ|馬鹿|アホ|使えない|何回言わせ|いい加減にしろ")),  # content-audit: ignore（検出語の一覧）
    ("いらだち", re.compile(r"何度も|何回も|言わせ(?:んな|るな|ない|る|て)|だから(?:\s|$|言)|じゃん|じゃねえ|んだよ|だろ[うお]?[?？]|だぞ[?？]?|すんな|しろよ|やれよ|とけよ|いい加減|ちゃんと|真面|マジで|無駄|バグ設計|勝手に|反抗|聞いてない|読み解け|理解しろ|考えろ|言ってんじゃん|言ってるだろ")),
    ("急かし", re.compile(r"早く|まだ[?？]|いつ(?:終わ|でき|なったら)|遅い|遅すぎ|時間かか|待たせ|さっさと|今すぐ")),
    ("失望", re.compile(r"また(?:か|同じ|それ|だ)|前も|同じこと|直ってない|変わってない|結局|期待外れ|がっかり")),
    ("承認", re.compile(r"任せる|いいよ|いいぞ|\bOK\b|おk|おっけ|それで(?:いい|進め)|よし|助かる|ありがと|さすが|いいね|進めて|やって(?:いい|よし)|頼む")),
]
GUIDE = {
    "怒り": "謝らない、言い訳しない、聞き返さない。今の一言をそのまま実行し、返答は 1〜2 行。やらなかった理由を 1 行で書き、lessons に 1 件足す",
    "いらだち": "謝らない、言い訳しない、聞き返さない。今の一言をそのまま実行し、返答は 1〜2 行。同じ確認を繰り返さない",
    "急かし": "残りは裏（run_in_background、Agent）に出し、できた分を ❕ 1 行で返す。予定を書かない",
    "失望": "同じ失敗。前回の失敗を lessons に 1 件書き、今回は動かして確かめてから出す。言い訳しない",
    "承認": "確認を増やさない。次の成果物へ進む。返事は成果物で",
}
# 語は、英数字の並び・カタカナの並び・漢字の並びで切る（ひらがなは助詞と活用なので切り捨てる）
WORD = re.compile(r"[A-Za-z0-9#_.-]{2,}|[ァ-ヶー]{2,}|[一-龥々]+")
STOP = {"Ciel", "PR", "リポジトリ", "スキル", "セッション", "ユーザー", "今", "前", "後", "俺", "私", "何", "事", "物", "方", "回", "度", "日", "分"}


def words(text: str) -> set:
    return {w for w in WORD.findall(text) if w not in STOP}


def previous_prompts(path: str, n: int = 3):
    """最後の人の発言より前の、人の発言 n 件（通知とフックの差し戻しは除く）。"""
    out = []
    try:
        for line in open(path, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("type") != "user":
                continue
            c = e.get("message", {}).get("content", "")
            if not isinstance(c, str):
                if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                    continue
                c = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            c = re.sub(r"<system-reminder>.*?</system-reminder>", "", c, flags=re.S).strip()
            if c and not SYSTEMISH.search(c):
                out.append(c)
    except OSError:
        return []
    return out[-n:]


def repeated(prompt: str, prev: list, cue: bool = False) -> int:
    """同じ指示が何回目か（今の一言を含む）。前の一言と 2 語以上が重なれば同じ指示。いらだちの手がかりがあれば 1 語でも数える。"""
    now = words(prompt)
    if not now:
        return 1
    count = 1
    for p in prev:
        shared = now & words(p)
        if len(shared) >= 2 or (cue and shared):
            count += 1
    return count


def classify(prompt: str):
    head = prompt[:600]
    for name, rx in CUES:
        m = rx.findall(head)
        if m:
            return name, [x if isinstance(x, str) else x[0] for x in m][:3]
    return "", []


def record(session_id: str, row: dict) -> None:
    base = os.environ.get("USER_EMOTION_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "user-emotion"
    try:
        root.mkdir(parents=True, exist_ok=True)
        with open(root / "metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": int(time.time()), "session": re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:40], **row}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("USER_EMOTION_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = str(d.get("prompt") or "")
    if not prompt.strip() or SYSTEMISH.search(prompt):
        return 0
    emotion, cues = classify(prompt)
    path = d.get("transcript_path")
    times = repeated(prompt, previous_prompts(path), emotion in ("いらだち", "怒り")) if path and os.path.isfile(path) else 1
    record(d.get("session_id", ""), {"emotion": emotion or "なし", "cues": cues, "repeated": times})
    parts = []
    if emotion:
        parts.append(f"感情: {emotion}（手がかり: " + "、".join(f"「{c}」" for c in cues) + "）。" + GUIDE[emotion])
    if times >= 2:
        parts.append(f"同じ指示が {times} 回目。前回やらなかった理由を 1 行で書き、今すぐやる。聞き返さない")
    if parts:
        print("。".join(parts) + "（user-emotion。感情を言い当てて返さない）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
