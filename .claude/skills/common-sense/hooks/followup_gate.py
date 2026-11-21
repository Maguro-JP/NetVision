#!/usr/bin/env python3
"""Stop フック: 当たり前の次の手順を始めずに、❔ で終えるのを差し戻す。

ユーザーの指摘: AI が当たり前のことをやってくれない。
見るのは 4 つがそろったとき。組は references/followups.md の「機械で見る組」の表から読む（機械で見られる組だけ）。
  1. このターンで、表の「したこと」に当たる呼び出しが成功している（例: PR のマージ）
  2. そのあとに、表の「次の手順」に当たる呼び出しが無い（例: 配布の開始）
  3. そのあとの返答に「❕ 次: …」の行が無い
  4. 返答が ❔ で終わっている（次の手順を始めずに、ユーザーの返事を待っている）
止められた呼び出し（フックの差し戻し、エラー）と、結果のまだ無い呼び出しは、成功に数えない。
表が無い・読めないときは何もしない。

環境変数:
  COMMON_SENSE_OFF=1   止める
"""

import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _cs  # noqa: E402

TABLE = Path(__file__).resolve().parent.parent / "references" / "followups.md"
HEAD = "## 機械で見る組"
NEXT_LINE = re.compile(r"^\s*[❕!]\s*次[:：]\s*\S", re.M)
QUESTION = re.compile(r"^\s*[❔?]\s*\S", re.M)
# PreToolUse のフックに止められた結果。is_error が付かない形でも文に出る
BLOCKED = re.compile(r"hook error|PreToolUse[^\n]{0,60}(?:blocked|denied|returned)|blocking hook|<tool_use_error>", re.I)
NOT_DONE = re.compile(r'"merged"\s*:\s*false')


def rows(text: str) -> list:
    """「機械で見る組」の表を読む。列は 組・したこと・次の手順・差し戻しの文。正規表現の「|」は表の中で \\| と書いてある。"""
    out, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            inside = line.strip() == HEAD
            continue
        if not inside or not line.lstrip().startswith("|"):
            continue
        cells = [c.strip().strip("`").replace("\\|", "|") for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        if len(cells) < 4 or not cells[0] or set(cells[1]) <= set("-: ") or cells[1].startswith("したこと"):
            continue
        try:
            out.append({"name": cells[0], "did": re.compile(cells[1]), "next": re.compile(cells[2]), "msg": cells[3]})
        except re.error:
            continue  # 書き損じた行は飛ばす（フックを止めない）
    return out


def done(turn: dict, call: dict) -> bool:
    r = turn["results"].get(call["id"])
    return bool(r) and not r["error"] and not BLOCKED.search(r["text"][:2000]) and not NOT_DONE.search(r["text"][:2000])


def problem(turn: dict, last: str, table: list):
    calls = turn["calls"]
    for row in table:
        hits = [i for i, c in enumerate(calls) if row["did"].search(_cs.call_text(c)) and done(turn, c)]
        if not hits:
            continue
        at = hits[-1]
        if any(row["next"].search(_cs.call_text(c)) for c in calls[at + 1:]):
            continue
        after = "\n".join(t for n, t in turn["texts"] if n > at) + "\n" + last
        if NEXT_LINE.search(after) or not QUESTION.search(last or after):
            continue
        return row
    return None


def main() -> int:
    if _cs.off():
        return 0
    d = _cs.read_event()
    path = d.get("transcript_path")
    if d.get("stop_hook_active") or not path or not os.path.isfile(path):
        return 0
    try:
        table = rows(TABLE.read_text(encoding="utf-8"))
        turns = _cs.last_turns(path, 1)
    except OSError:
        return 0
    if not table or not turns:
        return 0
    last = d.get("last_assistant_message")
    row = problem(turns[-1], last if isinstance(last, str) else "", table)
    if row is None:
        return 0
    print(f"{row['msg']}。次の手順を始めるか、「❕ 次: <次の手順と、いま始めたこと>」を 1 行足す（common-sense）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
