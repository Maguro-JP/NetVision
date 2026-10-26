#!/usr/bin/env bash
# Windows の Setup.exe を Linux の上で組む（NSIS の makensis を使う。Windows の機械は要らない）。
#
#   ./packaging/make-setup-windows.sh [出力先]     dist/NetVision-Setup.exe を作る
#
# 中身は make-bundle-windows.sh で作る一式と同じ（Python 本体、依存、画面、起動役）。
# 一式を作ってから、それを packaging/windows/installer.nsi で Setup.exe に包む。
# 入れた先は %LOCALAPPDATA%\Programs\NetVision。スタートメニューとデスクトップから、アプリのウィンドウで開く。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$REPO/dist}"
command -v makensis >/dev/null || { echo "makensis がありません（apt install nsis）" >&2; exit 1; }

"$REPO/packaging/make-bundle-windows.sh" "$OUT"
# 一式は ZIP にして中のフォルダを消すので、ZIP を開き直して包む（ZIP と Setup.exe の中身をそろえる）
ZIP="$(ls "$OUT"/NetVision-*-windows.zip | sort | tail -1)"
WORK="$OUT/setup-build"
rm -rf "$WORK" && mkdir -p "$WORK"
python3 -m zipfile -e "$ZIP" "$WORK"
STAGE="$WORK/$(basename "$ZIP" .zip)"

echo "==> 同梱物の欠けを見ます"
# 欠けたまま組むと、入れた先で起動して初めて分かる。起動に要るものがそろっているかを、組む前に確かめる
missing=""
for f in python/python.exe python/pythonw.exe backend/scripts/launcher.py backend/scripts/standalone.py \
         backend/app/main.py frontend/dist/index.html lib/fastapi lib/uvicorn lib/webview NetVision-console.bat; do
  [ -e "$STAGE/$f" ] || missing="$missing $f"
done
[ -z "$missing" ] || { echo "同梱物が欠けています:$missing" >&2; exit 1; }

echo "==> Setup.exe を組みます"
VERSION="$(basename "$STAGE" | sed 's/^NetVision-//; s/-windows$//')+$(git -C "$REPO" rev-parse --short HEAD 2>/dev/null || echo dev)"
makensis -V2 -DSRC="$STAGE" -DOUT="$OUT/NetVision-Setup.exe" -DVERSION="$VERSION" "$REPO/packaging/windows/installer.nsi"
ls -l "$OUT/NetVision-Setup.exe"
