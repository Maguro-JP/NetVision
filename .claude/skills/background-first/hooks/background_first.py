#!/usr/bin/env python3
"""PreToolUse フック: 長いコマンドを前面で動かすのを止め、ターンが長いときに裏へ出すよう知らせる。

ユーザーが優先。ターンが動いている間、ユーザーの一言は次のターンまで届かない。だから長い処理は裏で動かし、ターンを短く終える。
- Bash で長いコマンド（precheck、validate、テスト一式、npm や pip、clone、scan、ビルド、配布）を run_in_background 無しで呼んだら止める（exit 2）。
  裏で動かし、出力はファイルに書く。終わると通知が来るので、待つ間は sleep も polling もしない
- このターンのツール呼び出しが 12 回を超えるごとに、残りを裏（run_in_background、subagent）に出して ❕ で終えるよう知らせる（additionalContext）

環境変数:
  BACKGROUND_FIRST_OFF=1   止める
  BACKGROUND_FIRST_EVERY=N 知らせる間隔（既定 12）
"""

import json
import os
import re
import sys

# コマンドの先頭（;、&&、||、| の区切りのあと。cd と環境変数の指定は飛ばす）に当てる
LONG = re.compile(
    r"^(?:"
    r"python3?\s+(?:-[A-Za-z]+\s+)*\S*(?:precheck|vuln-watch/scripts/(?:scan|bump)|distribute|syncrepo\d*)\.py"  # secret-leak-check の scan.py は速い
    r"|python3?\s+(?:-[A-Za-z]+\s+)*\S*sync\.py\s[^\n]*--apply"
    r"|(?:bash\s+|sh\s+|\./)?\S*validate\.sh"
    r"|python3?\s+-m\s+(?:pytest|unittest\s+discover)|pytest\b"
    r"|npm\s+(?:test|ci|install|i\b|run\s+(?:build|test|e2e))|pnpm\s+(?:test|install|i\b|build)|yarn\s+(?:test|install|build)"
    r"|npx\s+(?:playwright|vitest|jest|tsc)|vitest\b|jest\b|tsc\b"
    r"|pip3?\s+install|uv\s+(?:pip\s+install|sync)|poetry\s+install|apt(?:-get)?\s+install"
    r"|git\s+clone|git\s+fetch\s+--all"
    r"|cargo\s+(?:build|test)|go\s+(?:test|build)|docker\s+(?:build|compose)|make\b"
    r")"
)
PREFIX = re.compile(r"^(?:cd\s+\S+\s*(?:&&|;)\s*|[A-Za-z_][A-Za-z0-9_]*=\S*\s+|time\s+|nohup\s+)+")
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")


def long_segments(cmd: str):
    out = []
    for seg in SPLIT.split(cmd or ""):
        s = PREFIX.sub("", seg.strip())
        s = re.sub(r"^(?:do|then|else)\s+", "", s)  # for … ; do <コマンド>; done の中も見る
        if LONG.search(s):
            out.append(s[:60])
    return out


def calls_this_turn(path: str, tail_bytes: int = 400_000) -> int:
    """transcript の末尾から、最後の人の発言より後のツール呼び出しの数。"""
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            lines = f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    n = 0
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            if isinstance(c, str) or not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                break
        elif e.get("type") == "assistant" and not isinstance(c, str):
            n += sum(1 for x in c if isinstance(x, dict) and x.get("type") == "tool_use")
    return n


def main() -> int:
    if os.environ.get("BACKGROUND_FIRST_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    tool, ti = d.get("tool_name", ""), d.get("tool_input") or {}
    if tool == "Bash" and not ti.get("run_in_background"):
        found = long_segments(str(ti.get("command", "")))
        if found:
            print(f"長いコマンド「{found[0]}」を前面で動かしている。run_in_background: true で裏に出し、出力は `> <scratchpad>/<名前>.log 2>&1; echo exit=$?` でファイルに書く。"
                  "終わると通知が来るので、待たずに ❕ 1 行でターンを終える。ユーザーの一言が先（background-first）", file=sys.stderr)
            return 2
    try:
        every = max(1, int(os.environ.get("BACKGROUND_FIRST_EVERY", "12")))
    except ValueError:
        every = 12
    path = d.get("transcript_path")
    if path and os.path.isfile(path):
        n = calls_this_turn(path)
        if n and n % every == 0:
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "additionalContext":
                  f"このターンのツール呼び出しが {n} 回。ユーザーの一言はターンが終わるまで届かない。残りは裏（run_in_background、Agent の run_in_background、子のセッション）に出し、❕ 1 行で終える（background-first）"}},
                  ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
