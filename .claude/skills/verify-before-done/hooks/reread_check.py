#!/usr/bin/env python3
"""Stop フック: 書いたあとに読み直していない返答と、差分を読まずにコミットした返答を差し戻す。

AI が作ったものには間違いがある。書いた直後の自分は、自分の書いたものを正しいと思っていて、
行番号で置き換えた sed が文を壊しても、表に書いた値がユーザーの意図と違っても、気づかずに「直した」と言う。
だから、最後の編集のあとに差分かそのファイルを読み、動かし、コミットの前に差分を読むことを機械で求める。

見るもの（このターンのツールの呼び出しの順）:
- 編集: Edit / Write / MultiEdit / NotebookEdit、または Bash の sed -i・python3 - <<・cat >・>>・tee・write_text
- 読み直し: Read / Grep、または Bash の git diff（--stat や --name-only は数えない）・git show・sed -n・cat・head・tail・grep -n、
  proofread.py・terms.py・docs_check.py・audit.py の差分の検査
- 動かす: validate.sh・precheck.py・test_*.py・pytest・unittest・npm test・hooks-smoke・test.sh
差し戻すのは、編集があるのに最後の編集のあとに読み直しも実行も無いとき、Markdown を編集したのに文を読んでいないとき、
git commit があるのにそのターンで差分を読んでいないとき、作業や完了の主張があるのに疑いの 1 行（間違っているかもしれない所、
確かめていないこと）が無いとき。「読み直していない」と書いてあれば読み直しの差し戻しはしない（正直な方が良い）。

環境変数:
  REREAD_CHECK_OFF=1      止める
  DONE_CHECK_STATE=dir    数の保持先（done_check と同じ。既定は ~/.claude/state/verify-before-done）
"""

import json
import os
import re
import sys
import time
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
BASH_EDIT = re.compile(r"\bsed\s+-i\b|python3?\s+-\s*<<|\bcat\s*>|(?<![|&<>])>>\s*\S|\btee\b|write_text\(|\.write\(")
TEXT_READ = re.compile(
    r"\bgit\s+diff\b(?![^\n;|&]*--(?:stat|name-only|name-status|shortstat|numstat))|\bgit\s+show\b(?![^\n;|&]*--(?:stat|name-only))"
    r"|\bsed\s+-n\b|\bcat\s+(?![>])\S|\bhead\b|\btail\b|\bgrep\s+-n\b|\bawk\b|proofread\.py|terms\.py|docs_check\.py|audit\.py"
)
CHECK_RUN = re.compile(r"validate\.sh|precheck\.py|test_\w+\.py|\bpytest\b|\bunittest\b|npm test|hooks-smoke|\btest\.sh\b")
COMMIT = re.compile(r"\bgit\s+commit\b(?![^\n;|&]*--amend)|\bgit\s+commit\s+--amend\b")
HONEST = re.compile(r"読み直していない|読み直していません|読んでいない|読んでいません|差分を見ていない|確かめていない")
# 疑いの 1 行。常に自分が間違っていると疑う。作業や主張のある返答に、間違っているかもしれない所と確かめていないことを書く
DOUBT = re.compile(r"疑い|確かめていない|確認していない|見ていない|読んでいない|読み直していない|間違っているかも|間違いがあり得る|私の推測|まだ分からない|分かっていない")
CLAIM = re.compile(r"直しました|作りました|足しました|入れました|通りました|マージしました|push しました|配りました|できました|完了")
MD = re.compile(r"\.md\b")


def text_of(c) -> str:
    if isinstance(c, str):
        return c
    return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")


def this_turn(path: str):
    """(ツールの呼び出し [(名前, 入力)] の順, 自分の文)。最後の人の発言から後だけ。"""
    calls, texts = [], []
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if isinstance(c, str) or not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                calls, texts = [], []
            continue
        if e.get("type") != "assistant":
            continue
        if not isinstance(c, str):
            for x in c:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    calls.append((str(x.get("name", "")), x.get("input") or {}))
        t = text_of(c)
        if t.strip():
            texts.append(t)
    return calls, "\n".join(texts)


def classify(name: str, inp: dict):
    """(編集か, Markdown の編集か, 文を読んだか, 動かしたか, コミットか)"""
    if name in EDIT_TOOLS:
        p = str(inp.get("file_path") or inp.get("notebook_path") or "")
        return True, bool(MD.search(p)), False, False, False
    if name in ("Read", "Grep"):
        return False, False, True, False, False
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        edit = bool(BASH_EDIT.search(cmd))
        return edit, edit and bool(MD.search(cmd)), bool(TEXT_READ.search(cmd)), bool(CHECK_RUN.search(cmd)), bool(COMMIT.search(cmd))
    return False, False, False, False, False


def judge(calls):
    """差し戻す理由の一覧。"""
    rows = [classify(n, i) for n, i in calls]
    edits = [k for k, r in enumerate(rows) if r[0]]
    reasons = []
    if edits:
        last = edits[-1]
        after = rows[last:]  # 同じ Bash の中の読み直しも数える（中の順番は分からない）
        reread = any(r[2] for r in after)
        ran = any(r[3] for r in after)
        md = any(r[1] for r in rows)
        if not reread and not ran:
            reasons.append("書いたあとに読み直していない。最後の編集のあとに、差分（git diff -- <ファイル>）かそのファイル（Read、sed -n）を読むか、テスト・validate・precheck を動かしてから終える")
        elif md and not reread:
            reasons.append("文書（.md）を書いたのに、文を読んでいない。差分（git diff -- <ファイル>）かそのファイル（Read、sed -n）を読む。validate は文の壊れを拾わない")
    commits = [k for k, r in enumerate(rows) if r[4]]
    if commits and not any(r[2] for r in rows[:commits[-1] + 1]):
        reasons.append("差分を読まずにコミットしている。git commit の前に git diff --cached か、変えたファイルを読む（--stat は読んだことにならない）")
    return reasons, {"edits": len(edits), "md": any(r[1] for r in rows), "reread": any(r[2] for r in rows), "ran": any(r[3] for r in rows), "commits": len(commits)}


def doubt_missing(text: str, stats: dict) -> bool:
    """作業（編集・コミット）か完了の主張があるのに、疑いの 1 行が無い。常に自分が間違っていると疑う。"""
    worked = stats["edits"] > 0 or stats["commits"] > 0 or bool(CLAIM.search(text))
    return worked and not DOUBT.search(text)


def record(session_id: str, row: dict) -> None:
    base = os.environ.get("DONE_CHECK_STATE")
    root = Path(base) if base else Path.home() / ".claude" / "state" / "verify-before-done"
    try:
        root.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(time.time()), "session": re.sub(r"[^A-Za-z0-9_-]", "", session_id or "")[:40], **row}
        with open(root / "reread.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        pass


def main() -> int:
    if os.environ.get("REREAD_CHECK_OFF") == "1":
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
        calls, text = this_turn(path)
    except OSError:
        return 0
    if not calls:
        return 0
    reasons, stats = judge(calls)
    honest = bool(HONEST.search(text))
    if honest:
        reasons = []
    if doubt_missing(text, stats):
        reasons.append("間違っているかもしれない所と、確かめていないことを ❕ で 1 行書く（例: ❕ 疑い: …は読み直したが動かしていない）。常に自分が間違っていると疑う")
    record(d.get("session_id", ""), {**stats, "blocked": bool(reasons), "honest": honest, "doubt": not doubt_missing(text, stats)})
    if reasons:
        print("。".join(reasons) + "（verify-before-done「読み直し」）。足りない行だけを足す。前の行を出し直さない", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
