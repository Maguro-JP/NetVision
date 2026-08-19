"""Vercel のサーバーレス関数として FastAPI を動かす入口。

PR ごとのプレビューで画面を触れるようにするためのもの。
閉域前提の本番運用は Docker Compose のままで、この経路は使わない。

デモモードでは書き込み先を /tmp に寄せる。サーバーレスではリポジトリ配下が
読み取り専用で永続領域も無いため。データが消えることは画面にも表示する。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# backend/ を import できるようにする
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# DB の指定が無ければ、一時領域の SQLite でデモとして動かす
if not os.environ.get("NETVISION_DATABASE_URL"):
    os.environ["NETVISION_DATABASE_URL"] = "sqlite:////tmp/netvision-demo.db"
    os.environ.setdefault("NETVISION_DEMO_MODE", "true")

if os.environ.get("NETVISION_DEMO_MODE", "").lower() in {"1", "true", "yes"}:
    # 資料ファイルの保管先も書き込み可能な場所へ
    os.environ.setdefault("NETVISION_DOCUMENT_STORAGE_PATH", "/tmp/netvision-documents")

# 画面と API は同じ場所から配るので CORS は要らない。開けておくと、他所のページから
# この API を叩ける状態になる（デモとはいえ、癖として残したくない）
os.environ.setdefault("NETVISION_CORS_ORIGINS", "[]")

from app.main import app as _app  # noqa: E402

# vercel.json の rewrite で /api/index に飛ばしているが、関数側に元のパスが
# そのまま渡るかは環境の挙動に依存する。渡っていない場合、素の 404 だと原因が
# 分かりにくいので、何が起きているかを名指しで返す。
_REWRITE_HINT = {
    "detail": (
        "リライトで元のパスが関数に渡っていません。vercel.json の rewrites を "
        "見直してください（/api/(.*) -> /api/index）。"
    ),
    "hint": "path_not_forwarded",
}


async def app(scope, receive, send):
    if scope["type"] == "http" and scope.get("path") in {"/api/index", "/api/index/"}:
        body = json.dumps(_REWRITE_HINT, ensure_ascii=False).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 500,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
        return
    await _app(scope, receive, send)


__all__ = ["app"]
