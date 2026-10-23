#!/usr/bin/env python3
"""UserPromptSubmit フック: ユーザーの指示と訂正を保持し、毎ターン差し込む。

CLAUDE.md やスキルに書いた決まりは、長い会話や圧縮のあとで効かなくなる。
ユーザーがこのセッションで言ったことも同じで、数ターン後には自分の判断が上に来てしまう。
そこで、指示や訂正らしい一言だけをファイルに残し、毎ターン新しい順に差し込む。
訂正の合図があったターンには、認めて言われた通りに直す手順を差し込む。

環境変数:
  FOLLOW_INSTRUCTIONS_OFF=1      止める
  FOLLOW_INSTRUCTIONS_STATE=dir  保持先（既定は ~/.claude/state/follow-instructions）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

KEEP = 8          # 差し込む件数
MAX_LEN = 120     # 1件の長さ
PASTE_LEN = 400   # これより長い一言は貼り付けとみなして保持しない

# 訂正の合図。「直して」「改善して」は普通の依頼にも多いので入れない
CORRECTION = re.compile(
    r"違う|ちがう|やめて|やめろ|勝手に|言った(?:通り|とおり|のに|だろ|でしょ|はず)|言ってる|言ってない"
    r"|聞いて(?:ない|いない|た\?|たか)|聞け|聞かない|それじゃない|そうじゃない|じゃなくて|ではなくて"
    r"|戻して|元に戻|しないで|するな|使うな|やるな|書くな|禁止|またか|また同じ|何度も|何回|何度"
    r"|イライラ|優先しない|従わ|改めない|貫く|センスがない|頼んでない|余計な"  # content-audit: ignore（検出語の一覧）
)
# 指示の合図。質問だけの一言は保持しない
DIRECTIVE = re.compile(
    r"[てで](?:ください|下さい|くれ|ね|よ|ほしい|欲しい)?$|[ろせ]$|すること|必ず|常に|毎回|今後|これから|以後|次から"
    r"|だけに|のみ|以外|使わない|しない|にして|で統一|優先|最初に|先に|お願い|頼む|にしろ|やめ"
)
SYSTEMISH = re.compile(r"^\s*<|wake reason|task-notification|SYSTEM NOTIFICATION")


def state_path(session_id: str) -> Path:
    base = os.environ.get("FOLLOW_INSTRUCTIONS_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "follow-instructions"
    root.mkdir(parents=True, exist_ok=True)
    sid = re.sub(r"[^A-Za-z0-9_-]", "", session_id or "") or "default"
    return root / f"{sid}.json"


def load(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(d, dict):
            d.setdefault("turns", 0)
            d.setdefault("entries", [])
            return d
    except (OSError, ValueError):
        pass
    return {"turns": 0, "entries": []}


def classify(prompt: str):
    """(保持するか, 訂正か)。"""
    if not prompt or SYSTEMISH.search(prompt) or len(prompt) > PASTE_LEN:
        return False, False
    correction = bool(CORRECTION.search(prompt))
    if correction:
        return True, True
    if prompt.rstrip().endswith(("?", "？")):
        return False, False
    return bool(DIRECTIVE.search(prompt)), False


def main() -> int:
    if os.environ.get("FOLLOW_INSTRUCTIONS_OFF") == "1":
        return 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    prompt = re.sub(r"\s+", " ", (data.get("prompt") or "")).strip()
    path = state_path(data.get("session_id") or "")
    st = load(path)
    st["turns"] += 1
    keep, correction = classify(prompt)
    if keep:
        st["entries"].append({"n": st["turns"], "t": prompt[:MAX_LEN], "c": correction, "ts": int(time.time())})
        st["entries"] = st["entries"][-KEEP:]
    try:
        path.write_text(json.dumps(st, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    lines = []
    if correction:
        lines.append(
            "訂正を受けた。1文で認める。言われた通りに直し、自分の案を混ぜない。理由と言い訳を書かない。"
            "直したものを ↩ で出す。代案は直したあとに ❔ で1行だけ（follow-instructions）。"
        )
        k = sum(1 for e in st["entries"] if e.get("c"))
        if k >= 2:
            lines.append(f"このセッションで訂正は {k} 回目。CLAUDE.md の「ユーザーの決めたこと」に1行足し、足したことを ↩ に書く。")
    if st["entries"]:
        lines.append("このセッションでユーザーが言ったこと（新しい順）。出す前にこれと照合する:")
        for e in reversed(st["entries"]):
            lines.append(f"- {'（訂正）' if e.get('c') else ''}{e['t']}")
    if lines:
        print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
