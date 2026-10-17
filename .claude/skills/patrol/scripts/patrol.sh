#!/usr/bin/env bash
# リポジトリを横断して、GitHub 側の状態を1行ずつ出す。
# 既定ブランチの最後のコミット、開いている PR、最後の Actions、枝の数、答え待ちの判断。
# セッションと Routine の状態は API では取れないので、SKILL.md の手順で別に見る。
# 使い方: patrol.sh <owner/repo> [<owner/repo> ...]
set -uo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
pending="$here/../../pending/scripts/pending.py"
[ $# -gt 0 ] || { echo "使い方: patrol.sh <owner/repo> ..." >&2; exit 2; }

ago() {  # ISO 時刻を「N時間前」にする
  local t; t=$(date -u -d "$1" +%s 2>/dev/null) || { echo "$1"; return; }
  local d=$(( $(date -u +%s) - t ))
  if [ $d -lt 3600 ]; then echo "$((d/60))分前"; elif [ $d -lt 86400 ]; then echo "$((d/3600))時間前"; else echo "$((d/86400))日前"; fi
}

for r in "$@"; do
  def=$(gh api "repos/$r" --jq .default_branch 2>/dev/null) || { echo "$r: 読めない"; continue; }
  last=$(gh api "repos/$r/commits/$def" --jq '[.commit.committer.date, (.commit.message|split("\n")[0])]|@tsv' 2>/dev/null)
  last_t=$(printf '%s' "$last" | cut -f1); last_m=$(printf '%s' "$last" | cut -f2 | cut -c1-50)
  prs=$(gh api "repos/$r/pulls?state=open&per_page=50" --jq '[.[]|"#\(.number) \(.head.ref)"]|join(", ")' 2>/dev/null)
  run=$(gh api "repos/$r/actions/runs?per_page=1" --jq '.workflow_runs[0] // empty | "\(.name) \(.conclusion // .status) \(.created_at)"' 2>/dev/null)
  br=$(gh api "repos/$r/branches?per_page=100" --jq 'length' 2>/dev/null)
  pend=$(python3 "$pending" list --count "$r" 2>/dev/null || echo "?")
  echo "== $r（$def）"
  echo "  最後のコミット: $(ago "$last_t")  $last_m"
  echo "  開いている PR : ${prs:-なし}"
  echo "  最後の Actions: ${run:-なし}"
  echo "  枝: ${br:-?} 本   答え待ち: ${pend} 件"
done
