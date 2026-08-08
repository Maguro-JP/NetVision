#!/usr/bin/env bash
# NetVision を起動する。
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export NETVISION_SERVE_FRONTEND="${NETVISION_SERVE_FRONTEND:-$HERE/frontend/dist}"
cd "$HERE/backend"
exec "$HERE/.venv/bin/python" -m scripts.standalone "$@"
