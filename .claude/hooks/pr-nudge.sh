#!/usr/bin/env bash
# PR を作った直後、または push した直後に「solo-pr-flow の判定に入る」を返す。
# PostToolUse の stdout は Claude に見えないので、additionalContext で返す。
set -uo pipefail
input="$(cat)"
python3 - "$input" <<'PY'
import json, re, sys
try:
    d = json.loads(sys.argv[1])
except Exception:
    sys.exit(0)
tool = d.get("tool_name", "")
cmd = str(d.get("tool_input", {}).get("command", ""))
hit = ("create_pull_request" in tool) or (tool == "Bash" and re.search(r"\bgh pr create\b|\bgit push\b", cmd))
if not hit:
    sys.exit(0)
msg = ("PR を作った、または push した。solo-pr-flow の判定に入る。"
       "自分のリポジトリで他人が見ていなければ、CI を待ってマージと後片付けまで進める。"
       "止まるなら条件名を挙げる。")
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": msg}}, ensure_ascii=False))
PY
