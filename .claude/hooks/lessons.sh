#!/usr/bin/env bash
# 失敗の記録から「場面」と「次から」の行だけを差し込む。
# 全文を読ませると重いので、索引と次の一手だけにする。
f=".claude/skills/lessons/LESSONS.md"
[ -f "$f" ] || exit 0
echo "失敗の記録（場面 → 次から）:"
awk '/^場面:/{s=$0; sub(/^場面: */,"",s)} /^次から:/{n=$0; sub(/^次から: */,"",n); print "- " s " → " n}' "$f"
