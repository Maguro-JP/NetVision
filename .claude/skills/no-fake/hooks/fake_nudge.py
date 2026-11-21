#!/usr/bin/env python3
"""PostToolUse フック（Edit、Write、MultiEdit）: この編集で足した行に重大の見せかけの形があれば、何行目のどの形かを 1 行で知らせる。

ユーザーの指摘: 出来ているように見せるだけ。わかっていてわざと壊す。
書いた直後に知らせれば、その場で直せる。ターンの終わりまで待つと、握りつぶしやテストの見せかけの上に次の変更が積まれる。
見るのは、この編集で足した行だけ（結果の originalFile と、今のファイルの中身を比べる）。前からある形と、動かしただけの行は言わない。
重大（握りつぶし、偽の検査、省略の置き場、テストのときだけの分岐、いつも通るテスト、条件なしの skip、テストや assert やフックの登録を消した、
検査をまるごと黙らせた）だけを出し、注意は出さない（数は fake_scan.py と precheck が出す）。止めはしない。
PostToolUse は成功したツールでしか動かない（失敗した書き込みには、足した行が無い）。

code-check の code_nudge.py が出す物（構文の誤り、脆弱性の型、情報の扱い）は出さない。`except: pass` は code-check では注意なので、
あちらのフックは言わない。形の表（このスキルと code-check の references/）と、fixtures や __mocks__ の下、文字列の中の見本には当てない。

環境変数:
  NO_FAKE_OFF=1      止める
  NO_FAKE_STATE=dir  数の置き場（既定は ~/.claude/state/no-fake）
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fake_scan as fs  # noqa: E402


def change(d: dict):
    """(根からの相対パス, 足した行と消した行, 今の中身)。見ない物は None。"""
    inp = d.get("tool_input") if isinstance(d.get("tool_input"), dict) else {}
    resp = d.get("tool_response") if isinstance(d.get("tool_response"), dict) else {}
    path = str(inp.get("file_path") or "")
    if not path or not os.path.isfile(path):
        return None
    rel = fs.rel_to_root(path)
    if not rel or fs.is_skipped(rel) or not fs.is_scanned(rel) or fs.is_vendored(fs.repo_root(path), rel):
        return None
    now = fs.read_text(Path(path))
    if now is None or fs.file_ignored(now):
        return None
    if "originalFile" in resp:
        old = resp["originalFile"] if isinstance(resp["originalFile"], str) else None
        return rel, fs.diff_texts(rel, old, now), now
    if d.get("tool_name") == "Write":
        return rel, fs.diff_texts(rel, None, now), now
    # 前の中身が無い版の結果。置き換えの前後の文から、足した行を今のファイルの中で探す
    edits = inp.get("edits") if isinstance(inp.get("edits"), list) else [inp]
    old_lines = {l.strip() for e in edits if isinstance(e, dict) for l in str(e.get("old_string") or "").splitlines()}
    new_lines = {l.strip() for e in edits if isinstance(e, dict) for l in str(e.get("new_string") or "").splitlines() if l.strip()} - old_lines
    fd = fs.FileDiff(rel, "M")
    fd.added = [(i, l, 0) for i, l in enumerate(now.splitlines(), 1) if l.strip() in new_lines]
    fd.removed = [(0, l, 0) for l in old_lines if l and l not in {x.strip() for x in now.splitlines()}]
    return rel, fd, now


def main() -> int:
    if os.environ.get("NO_FAKE_OFF") == "1":
        return 0
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        d = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return 0
    if not isinstance(d, dict) or d.get("tool_name") not in ("Edit", "Write", "MultiEdit"):
        return 0
    got = change(d)
    if got is None:
        return 0
    rel, fd, now = got
    found = [f for f in fs.dedupe(fs.scan_diffs([fd], lambda _: now)) if f.sev == "重大"]
    if not found:
        return 0
    fs.record("nudge", found, str(d.get("session_id") or ""))
    shown = "／".join((f"{f.line} 行 " if f.line else "") + f"{f.kind}（{f.what[:44]}）" for f in found[:3])
    more = f" ほか {len(found) - 3} 件" if len(found) > 3 else ""
    line = "「❕ 弱めた: <何>（理由）」" if all(f.kind in ("テスト", "弱めた") and f.id != "T02" for f in found) else "「❕ 見せかけ: <何>（理由）」"
    text = f"no-fake: {Path(rel).name} に重大 {len(found)} 件。{shown}{more}。いま直す。残すなら返答に{line}を 1 行書く（no-fake）"
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text[:400]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001 no-fake: ignore（知らせるだけのフックなので、壊れても書き込みの結果を止めない）
        sys.exit(0)
