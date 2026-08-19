#!/usr/bin/env bash
# Windows 向けの「インストール不要」バンドルを作る。
#
# 導入先で何もインストールできない環境向け。ZIP を展開して起動用のバッチを
# 叩くだけで動く。管理者権限は要らず、レジストリも Program Files も触らない。
#
# 仕組み:
#   - python.org の embeddable package（インストーラではなく ZIP）を同梱する
#   - 依存ライブラリは lib/ に展開済みで入れておく（閉域側で pip を動かさない）
#   - 画面はビルド済みを同梱（Node.js 不要）
#
# ネットに繋がる端末で実行してください。
set -euo pipefail

# Windows には python3 コマンドが無い（python.org 版は python、Microsoft Store 版は py）。
# Linux/macOS では逆に python が無いことがある。実際に動くものを1つ選ぶ。
# Windows の「アプリ実行エイリアス」は python という名前で存在だけして中身が無く、
# 実行するとストアが開いて終わるため、バージョンを聞いて答えるかどうかで確かめる。
PY=""
for candidate in python3 python py; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; sys.exit(0)" >/dev/null 2>&1; then
    PY="$candidate"
    break
  fi
done
if [ -z "$PY" ]; then
  cat >&2 <<'MSG'
Python が見つかりません。

  この作業をする側（ネットに繋がるパソコン）には Python が必要です。
  https://www.python.org/downloads/ から 3.11 以上を入れてください。
  インストール画面の最初にある「Add python.exe to PATH」に必ずチェックを
  入れてください。入れたあとは Git Bash を一度閉じて開き直します。

  ※ 配る先のパソコンには Python は要りません。この ZIP に同梱されます。
MSG
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "npm が見つかりません。https://nodejs.org/ の LTS 版を入れ、Git Bash を開き直してください。" >&2
  exit 1
fi

PY_VERSION="${PY_VERSION:-3.11.9}"
PY_TAG="cp311"          # embeddable のバージョンと合わせること
PY_ABI="311"

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(date +%Y%m%d)"
OUT="${1:-$REPO/dist}"
STAGE="$OUT/NetVision-$VERSION-windows"

echo "==> 画面をビルドします"
cd "$REPO/frontend"
npm ci >/dev/null
npm run build >/dev/null

echo "==> 組み立てます"
rm -rf "$STAGE"
mkdir -p "$STAGE"/{python,lib,backend,frontend,docs}

cp -r "$REPO/backend/app" "$STAGE/backend/app"
cp -r "$REPO/backend/scripts" "$STAGE/backend/scripts"
# 表を作る手順（マイグレーション）も持ち込む。これが無いと、初回起動で
# DB を作れずに落ちる
cp -r "$REPO/backend/alembic" "$STAGE/backend/alembic"
cp "$REPO/backend/alembic.ini" "$STAGE/backend/"
cp -r "$REPO/frontend/dist" "$STAGE/frontend/dist"
cp "$REPO/docs/INSTALL.md" "$REPO/docs/OPERATIONS.md" "$STAGE/docs/" 2>/dev/null || true
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> インストール不要の Python を取得します（$PY_VERSION）"
PY_ZIP="$OUT/python-$PY_VERSION-embed-amd64.zip"
if [ ! -f "$PY_ZIP" ]; then
  if ! curl -fSL -o "$PY_ZIP" \
    "https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-embed-amd64.zip"; then
    rm -f "$PY_ZIP"
    cat >&2 <<MSG

Python の取得に失敗しました。

  社内のプロキシや通信制限で python.org に届いていない可能性があります。
  別の端末で次のファイルを手で落として、この場所に置き直してから、
  もう一度このスクリプトを実行してください。

    取得元 : https://www.python.org/ftp/python/$PY_VERSION/python-$PY_VERSION-embed-amd64.zip
    置き場 : $PY_ZIP
MSG
    exit 1
  fi
fi
"$PY" -c "
import zipfile, sys
zipfile.ZipFile('$PY_ZIP').extractall('$STAGE/python')
print('  展開しました')
"

# embeddable Python は既定で同梱ライブラリしか見ない。
# 依存を置く lib と、アプリ本体のある backend を探索先に加える。
cat > "$STAGE/python/python$PY_ABI._pth" <<PTH
python$PY_ABI.zip
.
..\\lib
..\\backend
import site
PTH

echo "==> 依存ライブラリを Windows 向けに集めます"
# 閉域側で pip を動かさずに済むよう、あらかじめ展開した状態で入れておく
"$PY" -m pip install --quiet --target "$STAGE/lib" \
  --platform win_amd64 --python-version "${PY_VERSION%.*}" --only-binary=:all: \
  --implementation cp --abi "$PY_TAG" \
  fastapi sqlalchemy alembic pydantic pydantic-settings python-dateutil \
  openpyxl python-multipart jinja2 uvicorn tzdata
find "$STAGE/lib" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

# 起動用バッチと説明書きを作る。
#
# ファイル名は ASCII にしておく。日本語名は ZIP の文字コードの扱いが
# 解凍ツールごとに違い、Windows 側で化けることがあるため。
# バッチの中身は cmd が既定で解釈する CP932、説明書きはメモ帳が確実に
# 読める UTF-8 BOM 付きで、いずれも改行は CRLF にする。
"$PY" - "$STAGE" "$PY_VERSION" "$VERSION" <<'PYEOF'
import sys
from pathlib import Path

stage, py_version, version = Path(sys.argv[1]), sys.argv[2], sys.argv[3]

bat = """@echo off
rem NetVision を起動します。インストールは不要です。
rem 黒い画面は出しません。小さな窓が出るので、終了はそこから行います。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\\dist
start "" "%~dp0python\\pythonw.exe" -m scripts.launcher
"""
(stage / "NetVision.bat").write_bytes(bat.replace("\n", "\r\n").encode("cp932"))

diag = """@echo off
rem うまく起動しないときに使います。黒い画面のまま動かし、
rem 何が起きているかをその場に表示します。
setlocal
cd /d "%~dp0"
set NETVISION_SERVE_FRONTEND=%~dp0frontend\\dist
echo NetVision を、様子が見える形で起動します。
echo 止めるときは、この画面を閉じてください。
echo.
"%~dp0python\\python.exe" -m scripts.standalone
echo.
echo 終了しました。上に出ている内容を控えてください。
pause
"""
(stage / "NetVision-check.bat").write_bytes(
    diag.replace("\n", "\r\n").encode("cp932")
)

readme = f"""NetVision （インストール不要版）
====================================

このフォルダは、パソコンに何もインストールせずに動きます。
管理者権限は不要で、レジストリや Program Files も変更しません。

■ 使い方

  1. このフォルダを、デスクトップなど書き込める場所に置く
  2. 「NetVision.bat」をダブルクリック
  3. ブラウザが開きます（開かない場合は http://127.0.0.1:8000 ）

  小さな窓が1つ出ます。止めるときは、その窓の「終了する」を押すか、
  窓を閉じてください。黒い画面は出ません。

■ うまく起動しないとき

  「NetVision-check.bat」をダブルクリックしてください。黒い画面のまま動かし、
  何が起きているかをその場に表示します。表示された内容を控えてください。
  起動の記録は次の場所にも残ります。

    C:\\Users\\（あなたのユーザー名）\\.netvision\\launcher.log

■ データの保存先

  C:\\Users\\（あなたのユーザー名）\\.netvision

  このフォルダをコピーすればバックアップになります。
  アプリのフォルダを入れ替えても、データはそのまま残ります。

■ ネットワークについて

  外部への通信は一切行いません。
  自分のパソコンからのみアクセスできる設定のため、
  同じ社内ネットワークの他の人からは見えません。

■ 同梱物

  python\\    インストール不要版の Python {py_version}（python.org 配布）
  lib\\       このアプリが使うライブラリ
  backend\\   アプリ本体
  frontend\\  画面
  docs\\      導入手順書・運用手順書

版数: {version}
"""
(stage / "README.txt").write_bytes(
    b"\xef\xbb\xbf" + readme.replace("\n", "\r\n").encode("utf-8")
)
print("  起動用バッチと説明書きを作りました")
PYEOF

echo "==> ZIP に固めます"
cd "$OUT"
rm -f "NetVision-$VERSION-windows.zip"
"$PY" -c "
import shutil
shutil.make_archive('NetVision-$VERSION-windows', 'zip', '.', 'NetVision-$VERSION-windows')
"
rm -rf "$STAGE"

echo
echo "できました: $OUT/NetVision-$VERSION-windows.zip"
echo "展開して NetVision.bat をダブルクリックするだけで動きます。"
