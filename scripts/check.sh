#!/usr/bin/env bash
# CI と同じ手順を、手元で一度に流す。
#
# GitHub Actions には無料枠があり、ジョブは1分未満でも1分として数えられる。
# 下書きのあいだは CI を走らせない代わりに、ここで同じものを通す。
# 見る順番も CI と同じにしてある。CI で落ちるものは、ここでも落ちる。
#
#   ./scripts/check.sh            全部
#   ./scripts/check.sh backend    backend だけ
#   ./scripts/check.sh frontend   frontend だけ
#
# 失敗したらそこで止まり、終了コードを返す。出力は捨てない。

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
target="${1:-all}"
failed=0

step() {
  printf '\n=== %s\n' "$1"
}

# backend の python は .venv を優先する。入っていなければ python3 で試す
python_bin() {
  if [ -x "$root/backend/.venv/bin/python" ]; then
    echo "$root/backend/.venv/bin/python"
  else
    echo "python3"
  fi
}

run_backend() {
  local python
  python="$(python_bin)"
  cd "$root/backend"
  step "backend: ruff check"
  "$python" -m ruff check .
  step "backend: ruff format --check"
  "$python" -m ruff format --check .
  step "backend: pytest"
  "$python" -m pytest -q
}

run_frontend() {
  cd "$root/frontend"
  step "frontend: typecheck"
  npm run typecheck
  step "frontend: test"
  npm test
  step "frontend: build"
  npm run build
}

case "$target" in
  backend) run_backend ;;
  frontend) run_frontend ;;
  all)
    run_backend
    run_frontend
    ;;
  *)
    echo "使い方: ./scripts/check.sh [backend|frontend]" >&2
    exit 2
    ;;
esac

printf '\n=== 全部通りました（CI と同じ手順）\n'
exit $failed
