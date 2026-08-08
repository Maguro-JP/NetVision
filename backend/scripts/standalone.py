"""1台完結で起動する。1人で実データを試すとき用。

    python -m scripts.standalone

Postgres も nginx も要らない。データは1つの SQLite ファイルに入るので、
そのファイルをコピーすればバックアップになる。

既定では **127.0.0.1 にだけ** 待ち受ける。実機の Config を入れる以上、
同じLAN の他の端末から見えてしまうのは避けたいため。まだ認証が無いので、
ここを緩めると誰でも中身を見られる状態になる。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# リポジトリ直下（backend/ の親）
REPO = Path(__file__).resolve().parent.parent.parent
DEFAULT_DATA = Path.home() / ".netvision"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NetVision を1台完結で起動する")
    parser.add_argument("--port", type=int, default=8000, help="待ち受けポート（既定 8000）")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="待ち受けアドレス。既定は自分の端末からのみ（認証がまだ無いため）",
    )
    parser.add_argument(
        "--data-dir",
        default=str(DEFAULT_DATA),
        help=f"DB と資料ファイルの置き場所（既定 {DEFAULT_DATA}）",
    )
    parser.add_argument("--demo", action="store_true", help="デモデータを入れて起動する")
    args = parser.parse_args(argv)

    data_dir = Path(args.data_dir).expanduser()
    data_dir.mkdir(parents=True, exist_ok=True)
    db_path = data_dir / "netvision.db"

    os.environ.setdefault("NETVISION_DATABASE_URL", f"sqlite:///{db_path}")
    os.environ["NETVISION_AUTO_CREATE_SCHEMA"] = "true"
    os.environ.setdefault("NETVISION_DOCUMENT_STORAGE_PATH", str(data_dir / "documents"))
    if args.demo:
        os.environ["NETVISION_DEMO_MODE"] = "true"

    dist = REPO / "frontend" / "dist"
    if dist.is_dir():
        os.environ.setdefault("NETVISION_SERVE_FRONTEND", str(dist))
    else:
        print(
            "画面がまだビルドされていません。先に次を実行してください:\n"
            "    cd frontend && npm ci && npm run build",
            file=sys.stderr,
        )
        return 1

    import uvicorn

    print(f"データの置き場所 : {data_dir}")
    print(f"DB               : {db_path}")
    print(f"画面             : http://{args.host}:{args.port}")
    if args.host not in {"127.0.0.1", "localhost"}:
        print(
            "  ※ 自分の端末以外からも見える設定です。"
            "まだ認証が無いので、置き場所と回線に注意してください"
        )
    print("止めるときは Ctrl+C")

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
