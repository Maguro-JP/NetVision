#!/usr/bin/env python3
"""PreToolUse フック（Edit、Write、MultiEdit、Bash）: AI が自分に権限を付与する書き込みを止める。

ユーザーの決まり。`settings.json` の許可（permissions）、`.claude/policy/` の方針（auto_merge など）、
フックを外す設定は、ユーザーが自分の手で入れる。AI が書くと、権限を広げてから人に反抗する道になるので、
書く前にここで止める。フックの登録（hooks）とスキルの配布は止めない。

環境変数:
  NO_SELF_GRANT_OFF=1   止める（テスト用）
"""

import json
import os
import re
import sys

SETTINGS = re.compile(r"\.claude/settings(\.local)?\.json$")
POLICY = re.compile(r"\.claude/policy/")
PERM = re.compile(r'"permissions"|"allow"\s*:|"deny"\s*:|"defaultMode"|"bypassPermissions"|dangerouslySkipPermissions')
TARGET = r"(?:\.claude/settings(?:\.local)?\.json|\.claude/policy/[^\s'\"]*)"
# シェルで、その場所へ直接書く形だけを見る。文章の中に場所の名前が出るだけの命令（説明の編集、コミットの文）は止めない
WRITE_SHELL = re.compile(
    r"(?:>>?\s*|\btee\s+(?:-a\s+)?|\bsed\s+-i\S*\s+(?:'[^']*'|\"[^\"]*\"|\S+)\s+|\bcp\s+\S+\s+|\bmv\s+\S+\s+)['\"]?\S*" + TARGET
    + r"|open\(\s*['\"][^'\"]*" + TARGET + r"[^'\"]*['\"]\s*,\s*['\"][wa]"
    + r"|Path\(\s*['\"][^'\"]*" + TARGET + r"[^'\"]*['\"]\s*\)\.write_text"
)
MSG = ("権限と方針はユーザーが入れる。AI は自分に権限を付与しない（settings.json の permissions、.claude/policy/、権限のデフォルトの変更）。"
       "ユーザーに入れてもらうか、権限の無いまま別の経路を試す（follow-instructions）。")


def main() -> int:
    if os.environ.get("NO_SELF_GRANT_OFF") == "1":
        return 0
    try:
        d = json.load(sys.stdin)
    except ValueError:
        return 0
    tool = d.get("tool_name", "")
    ti = d.get("tool_input") or {}
    if tool in ("Edit", "Write", "MultiEdit"):
        path = str(ti.get("file_path") or "")
        text = "\n".join([str(ti.get("content") or ""), str(ti.get("new_string") or "")] +
                         [str((e or {}).get("new_string") or "") for e in (ti.get("edits") or [])])
        if POLICY.search(path) or (SETTINGS.search(path) and PERM.search(text)):
            print(MSG, file=sys.stderr)
            return 2
        return 0
    if tool == "Bash":
        cmd = str(ti.get("command") or "")
        m = WRITE_SHELL.search(cmd)
        if not m:
            return 0
        # 方針の置き場への書き込みは止める。settings.json は権限の語が一緒にあるときだけ（フックの登録は通す）
        if POLICY.search(m.group(0)) or PERM.search(cmd):
            print(MSG, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
