#!/usr/bin/env python3
"""Stop フック: 直していない致命的なバグがあるのに、別の開発の編集をして終えるのを差し戻す。

ユーザーの決まり: 何よりもバグ修正を優先させる。特に致命的なバグ、クラッシュや操作不能、破損や漏洩等は即修正版を出す。
帳簿に書いただけでは、次のターンにはもう別の作業をしている。致命的が残っている間は、ほかの開発を先に進めさせない。

差し戻すのは、次が全部そろったとき。
  1. 帳簿（docs/reports/bugs.md）に、重さが致命的で、状態が未修正か修正中の節がある（確認待ちは、返事を待つ間ほかへ進めるので見ない）
  2. このターンに、成功した開発の編集（Edit、Write、MultiEdit、NotebookEdit）がある。記録（docs/reports/、docs/knowledge/、LESSONS.md）と、
     リポジトリの外（scratchpad）への書き込みは、開発の編集に数えない
  3. その編集が、致命的の修正ではない。修正と見るのは、帳簿を書き換えた（Edit か bugs.py の --add と --set）、
     呼び出しにその節の番号がある、編集したファイルが修正のブランチ（名前に fix、hotfix、bug）にある、のどれか
  4. 返答に `❕ バグ: <先にしなかった理由>` の行が無い（ユーザーが別の作業を指示したときは、この 1 行で通る）
帳簿が無いリポジトリと、common-sense（会話の記録の読み手）が無いリポジトリでは、何もしない。

環境変数:
  BUG_FIRST_OFF=1   止める
"""

import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _bf  # noqa: E402

EDIT_TOOLS = ("Edit", "Write", "MultiEdit", "NotebookEdit")
ESCAPE = re.compile(r"^\s*[❕!]\s*バグ[:：]\s*\S", re.M)
FIX_BRANCH = re.compile(r"(?:^|[/_-])(?:fix|hotfix|bugfix|bug)(?:$|[/_-])", re.I)
RECORD = re.compile(r"(?:^|/)docs/(?:reports|knowledge)/|(?:^|/)LESSONS\.md$")
LEDGER_EDIT = re.compile(r"bugs\.py\b[^\n;|&]*\s--(?:add|set)\b")
# PreToolUse のフックに止められた結果。is_error が付かない形でも文に出る
BLOCKED = re.compile(r"hook error|PreToolUse[^\n]{0,60}(?:blocked|denied|returned)|blocking hook|<tool_use_error>", re.I)


def done(turn: dict, call: dict) -> bool:
    r = turn["results"].get(call["id"])
    return bool(r) and not r["error"] and not BLOCKED.search(r["text"][:2000])


def inside(root: Path, path: str):
    """リポジトリの根からの相対のパス。外（scratchpad、/tmp）なら None。"""
    p = Path(path)
    full = os.path.realpath(p if p.is_absolute() else root / p)
    base = os.path.realpath(root)
    if not path or (full != base and not full.startswith(base + os.sep)):
        return None
    return os.path.relpath(full, base).replace(os.sep, "/")


def branch_of(directory: Path, cache: dict) -> str:
    """そのディレクトリのブランチ。worktree で直しているときは、根のブランチと違う。"""
    d = directory
    while not d.exists() and d != d.parent:
        d = d.parent
    key = str(d)
    if key not in cache:
        try:
            # symbolic-ref は、まだコミットの無いブランチでも名前を返す（rev-parse --abbrev-ref HEAD は失敗する）。切り離された HEAD では空
            r = subprocess.run(["git", "-C", key, "symbolic-ref", "--short", "-q", "HEAD"], capture_output=True, text=True, timeout=5)
            cache[key] = r.stdout.strip() if r.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            cache[key] = ""
    return cache[key]


def other_edits(turn: dict, root: Path, fatal: list, call_text) -> list:
    """このターンの、致命的の修正ではない開発の編集（根からの相対のパス）。致命的に触ったターンなら空。"""
    ids = re.compile(r"(?<![\w-])(?:" + "|".join(re.escape(b["id"]) for b in fatal) + r")(?!\d)")
    edits, cache = [], {}
    for c in turn["calls"]:
        text = call_text(c)
        if ids.search(text):
            return []  # 呼び出しに、その節の番号がある（テストの名前、コミットメッセージ、帳簿の書き換え）
        if c["name"] == "Bash":
            if LEDGER_EDIT.search(text) and done(turn, c):
                return []
            continue
        if c["name"] not in EDIT_TOOLS or not done(turn, c):
            continue
        rel = inside(root, str(c["input"].get("file_path") or c["input"].get("notebook_path") or ""))
        if rel is None:
            continue
        if rel == str(_bf.LEDGER).replace(os.sep, "/"):
            return []
        if RECORD.search(rel):
            continue
        if FIX_BRANCH.search(branch_of((root / rel).parent, cache)):
            continue
        edits.append(rel)
    return edits


def main() -> int:
    if _bf.off():
        return 0
    d = _bf.read_event()
    path = d.get("transcript_path")
    if d.get("stop_hook_active") or not path or not os.path.isfile(path):
        return 0
    fatal = _bf.bugs.blocking_fatal(_bf.ledger_bugs())
    if not fatal:
        return 0
    cs = _bf.cs()
    if cs is None:
        return 0
    try:
        turns = cs.last_turns(path, 1)
    except OSError:
        return 0
    if not turns:
        return 0
    turn = turns[-1]
    last = d.get("last_assistant_message")
    said = "\n".join(t for _, t in turn["texts"]) + "\n" + (last if isinstance(last, str) else "")
    if ESCAPE.search(said):
        return 0
    edits = other_edits(turn, Path.cwd(), fatal, cs.call_text)
    if not edits:
        return 0
    more = f" ほか {len(edits) - 1} 件" if len(edits) > 1 else ""
    print(f"直していない致命的なバグ（{_bf.short(fatal[0])}）があるのに、別の編集（{edits[0]}{more}）で終えようとしている。"
          "先に直して修正版を出すか、帳簿の状態を変えるか、「❕ バグ: <先にしなかった理由>」を 1 行足す（bug-first）", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
