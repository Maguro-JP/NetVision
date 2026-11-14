#!/usr/bin/env bash
# 止まる前に、直前の応答に韓国語（ハングル）が混ざっていないか見る。
# 混ざっていれば exit 2 で止め、日本語で書き直させる。
# 漢字は日本語と中国語で重なるので、確実に判定できるハングルだけを見る。
set -uo pipefail
input="$(cat)"
case "$input" in *'"stop_hook_active":true'*) exit 0;; esac
python3 - "$input" <<'PY'
import json, re, sys
try:
    d = json.loads(sys.argv[1]); path = d.get("transcript_path")
except Exception:
    sys.exit(0)
if not path:
    sys.exit(0)
last = ""
try:
    for line in open(path, encoding="utf-8", errors="replace"):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "assistant":
            continue
        c = e.get("message", {}).get("content", [])
        if isinstance(c, str):
            last = c
        else:
            t = "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            if t.strip():
                last = t
except Exception:
    sys.exit(0)
hits = re.findall(r"[가-힯ᄀ-ᇿ㄰-㆏]+", last)
if hits:
    print("直前の応答に韓国語が混ざっています: " + " ".join(hits[:5]) + "\n日本語だけで書き直してください。", file=sys.stderr)
    sys.exit(2)
PY
