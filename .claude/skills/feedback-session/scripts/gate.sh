#!/usr/bin/env bash
# feedback-session の門。Stop フックとして動く。
# 印（.claude/feedback-session）のあるセッションで、この返答の中で書いた文が
# ★ ↩ ❕ ❔ で始まる 3 行以内でなければ exit 2 で止め、1 行に書き直させる。印は利用者が決めたもの。
# 指示だけでは了解の返事や経過が混ざり続けたので、最後に機械で見る。
# 途中の文（ツールを呼ぶ間に書いた文）も会話に出るので、返答の中の文は全部見る。
set -uo pipefail
input="$(cat)"
case "$input" in *'"stop_hook_active":true'*) exit 0;; esac
# 目印は .claude/feedback-session（題目）か .claude/quiet（auto-dev などの自律の作業）。無ければ何もしない
mark=""; for f in .claude/feedback-session .claude/quiet; do [ -f "$f" ] && { mark="$f"; break; }; done
[ -n "$mark" ] || exit 0
topic="$(head -1 "$mark" 2>/dev/null || true)"
python3 - "$input" "$topic" <<'PY'
import json, sys
try:
    d = json.loads(sys.argv[1]); path = d.get("transcript_path")
except Exception:
    sys.exit(0)
topic = sys.argv[2] or "<題目>"
if not path:
    sys.exit(0)
texts = []  # この返答（最後の利用者の発言のあと）に書いた文
try:
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        c = e.get("message", {}).get("content", [])
        if e.get("type") == "user":
            # ツールの結果も user として入る。人（か Routine）の発言だけで区切る
            if isinstance(c, str) or not any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
                texts = []
            continue
        if e.get("type") != "assistant":
            continue
        if isinstance(c, str):
            t = c
        else:
            t = "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
        if t.strip():
            texts.append(t)
except Exception:
    sys.exit(0)
lines = [l.strip() for t in texts for l in t.splitlines() if l.strip()]
bad = [l for l in lines if l[0] not in "★⭐↩⏎❕❗❔❓"]
if bad or len(lines) > 3:
    sample = (bad or lines)[0][:60]
    print(f"feedback-session: 会話に出す行は ★成果物 ↩返事 ❕1行報告 ❔質問 の4種類だけ（{len(lines)} 行、例: {sample}）。"
          f"了解・経過・予定は書かず、ツールを呼ぶ間に文を書かない。"
          f"「★ {topic} 回N: <何を変えてどうなったか1行>」に書き直す。成果物が無ければ「❕ {topic}: 落ちた N 件（<種類>）」。",
          file=sys.stderr)
    sys.exit(2)
PY
