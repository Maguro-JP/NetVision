"""1台完結で起動する。導入テストと、担当者1人での運用向け。

    python -m scripts.standalone

Postgres も nginx も要らない。データは1つの SQLite ファイルに入るので、
そのファイルをコピーすればバックアップになる。サーバーが無くても、
現場のノートPCで実データを入れて採用判断できる。

既定では 127.0.0.1 にだけ 待ち受ける。実機の Config を入れる以上、
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
#: DB と資料の置き場所。環境変数でも渡せる。
#: 端末によっては C ドライブに置けない（データを残さない決まりの端末など）ので、
#: 画面を触らずに、起動用のファイルの中だけで移せるようにしておく
DATA_DIR_ENV = "NETVISION_DATA_DIR"
DEFAULT_DATA = Path.home() / ".netvision"


def default_data_dir() -> Path:
    return Path(os.environ.get(DATA_DIR_ENV) or DEFAULT_DATA).expanduser()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="NetVision を1台完結で起動する")
    parser.add_argument("--port", type=int, default=8000, help="待ち受けポート（既定 8000）")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="待ち受けアドレス。既定は自分の端末からのみ（認証がまだ無いため）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help=f"DB と資料ファイルの置き場所（既定 {DEFAULT_DATA}、{DATA_DIR_ENV} でも指定できる）",
    )
    parser.add_argument("--demo", action="store_true", help="デモデータを入れて起動する")
    return parser


def synced(path) -> str | None:
    """その場所が、同期フォルダかネットワークの共有か。"""
    text = str(path)
    for needle in ("OneDrive", "Dropbox", "Google ドライブ", "GoogleDrive", "Box"):
        if needle.lower() in text.lower():
            return f"{needle} の中"
    if text.startswith("\\\\"):
        return "ネットワークの共有フォルダ"
    return None


def on_synced_folder() -> str | None:
    """アプリの置き場所が、同期フォルダやネットワークドライブか。

    OneDrive の中や Z: に置くと、Python が中間ファイルを書くたびに同期が走り、
    起動が戻ってこないことがある。実際に、起動しないという形で踏んだ。
    黙って遅いより、置き場所が悪いと言えたほうが早い。
    """
    where = synced(REPO)
    return f"{where}に置かれています" if where else None


def prepare(data_dir: str, *, demo: bool = False) -> Path:
    """置き場所を決めて、環境変数に反映する。

    起動の入口が2つ（黒い画面と、窓のある起動役）あるので、ここを共有する。
    片方だけ直すと、同じアプリなのに置き場所が違う、ということが起きる。
    """
    path = Path(data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    db_path = path / "netvision.db"

    os.environ.setdefault("NETVISION_DATABASE_URL", f"sqlite:///{db_path}")
    os.environ.setdefault("NETVISION_DOCUMENT_STORAGE_PATH", str(path / "documents"))
    if demo:
        os.environ["NETVISION_DEMO_MODE"] = "true"
    return path


def frontend_dist() -> Path | None:
    """ビルド済みの画面。無ければ None。"""
    dist = REPO / "frontend" / "dist"
    if not dist.is_dir():
        return None
    os.environ.setdefault("NETVISION_SERVE_FRONTEND", str(dist))
    return dist


NO_FRONTEND = (
    "画面がまだビルドされていません。先に次を実行してください:\n"
    "    cd frontend && npm ci && npm run build"
)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    data_dir = prepare(args.data_dir or str(default_data_dir()), demo=args.demo)
    db_path = data_dir / "netvision.db"

    if frontend_dist() is None:
        print(NO_FRONTEND, file=sys.stderr)
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
    warning = on_synced_folder()
    if warning:
        print(
            f"  ※ アプリが{warning}。中間ファイルは同梱済みなので動きますが、"
            "同期を止められる場所のほうが速く動きます"
        )
    where = synced(data_dir)
    if where:
        print(
            f"  ※ DB が{where}にあります。書いている最中に同期に持っていかれると"
            "壊れることがあるので、控え（データフォルダのコピー）をこまめに取ってください"
        )
    print("止めるときは Ctrl+C")

    uvicorn.run("app.main:app", host=args.host, port=args.port, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
