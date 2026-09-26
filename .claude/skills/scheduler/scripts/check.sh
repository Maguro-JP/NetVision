#!/usr/bin/env bash
# Routine を起こしたあと、リポジトリ側に書き込みが増えたかを待って確かめる。
# Routine の status は起きただけで緑になるので、根拠にしない。
# 使い方: check.sh <owner/repo> <ブランチ名の一部> [待つ秒数=240]
set -uo pipefail

repo="${1:?owner/repo}"
part="${2:?ブランチ名の一部}"
limit="${3:-240}"
url="https://github.com/${repo}.git"

before="$(git ls-remote --heads "$url" 2>/dev/null)" || { echo "ls-remote 失敗: $url"; exit 2; }

waited=0
while [ "$waited" -lt "$limit" ]; do
  sleep 10
  waited=$((waited+10))
  now="$(git ls-remote --heads "$url" 2>/dev/null)" || continue
  if [ "$now" != "$before" ] && echo "$now" | grep -q "$part"; then
    echo "増えた（${waited}秒）:"
    echo "$now" | grep "$part"
    exit 0
  fi
done

echo "${limit}秒待ったが ${part} を含むブランチに変化なし"
echo "$now" | grep "$part" || true
exit 1
