#!/usr/bin/env bash
# 閉域環境へ持ち込むためのオフラインバンドルを作る。
#
#   ネットに繋がる端末で実行 → 出来た tar.gz を閉域へ持ち込む
#
# 中身は「閉域側でネットを一切使わずに導入できる」ものだけ。
#   - ビルド済みの画面（frontend/dist）
#   - バックエンドのソース
#   - Python の依存を wheel で同梱（pip install --no-index で入る）
#   - 導入 / 起動スクリプトと手順書
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VERSION="$(date +%Y%m%d)"
OUT="${1:-$REPO/dist}"
STAGE="$OUT/netvision-$VERSION"

echo "==> 画面をビルドします"
cd "$REPO/frontend"
npm ci
npm run build

echo "==> バンドルを組み立てます"
rm -rf "$STAGE"
mkdir -p "$STAGE"/{backend,frontend,wheels,docs}

cp -r "$REPO/backend/app" "$STAGE/backend/app"
cp -r "$REPO/backend/scripts" "$STAGE/backend/scripts"
cp -r "$REPO/backend/alembic" "$STAGE/backend/alembic"
cp "$REPO/backend/alembic.ini" "$REPO/backend/pyproject.toml" "$STAGE/backend/"
cp -r "$REPO/frontend/dist" "$STAGE/frontend/dist"
cp "$REPO/docs/INSTALL.md" "$REPO/docs/OPERATIONS.md" "$STAGE/docs/" 2>/dev/null || true
cp "$REPO/packaging/install.sh" "$REPO/packaging/start.sh" "$STAGE/"
cp "$REPO/packaging/install.bat" "$REPO/packaging/start.bat" "$STAGE/"
cp "$REPO/packaging/netvision.service" "$STAGE/"
chmod +x "$STAGE"/*.sh

# __pycache__ は持ち込まない（環境が違うと邪魔になるだけ）
find "$STAGE" -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> Python の依存を wheel で集めます"
# 閉域側と同じ OS / Python で実行すること。違うと入らない wheel が混ざる。
#
# 依存を落とすだけでなく、**アプリ自身も wheel に固めておく**のが要点。
# ソースのまま持ち込むと閉域側でビルドが走り、その際 pip が setuptools を
# 取りに行って失敗する（ビルド分離のため同梱物からは解決されない）。
python3 -m pip wheel --wheel-dir "$STAGE/wheels" "$REPO/backend[desktop]" >/dev/null

# 念のため venv 作成直後に必要になるものも同梱しておく
python3 -m pip download --dest "$STAGE/wheels" pip setuptools wheel >/dev/null

cat > "$STAGE/VERSION" <<EOF
NetVision
版数      : $VERSION
作成日時  : $(date -Iseconds)
作成環境  : $(python3 -V 2>&1) / $(uname -sm)
EOF

echo "==> 固めます"
cd "$OUT"
tar czf "netvision-$VERSION.tar.gz" "netvision-$VERSION"
rm -rf "$STAGE"

echo
echo "できました: $OUT/netvision-$VERSION.tar.gz"
echo "これを閉域へ持ち込み、展開して ./install.sh を実行してください。"
