#!/usr/bin/env bash
# 閉域環境での導入。ネットには一切繋ぎません。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 が見つかりません。3.11 以上を先に入れてください。" >&2
  exit 1
fi

VER="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
case "$VER" in
  3.1[1-9]|3.[2-9]*) ;;
  *) echo "Python 3.11 以上が必要です（今: $VER）" >&2; exit 1 ;;
esac

echo "==> 仮想環境を作ります"
python3 -m venv "$HERE/.venv"

echo "==> 同梱の wheel から入れます（ネット未使用）"
# ビルド済みの wheel を入れるだけなので、閉域側でビルドは走らない
"$HERE/.venv/bin/pip" install --quiet --no-index --find-links "$HERE/wheels" "netvision-backend[desktop]"

echo
echo "導入できました。"
echo "  起動      : ./start.sh（アプリのウィンドウが開きます）"
echo "  データ    : \$HOME/.netvision"
echo
echo "常時起動させる（サーバーとして置く）場合は netvision.service を参考にしてください。"
