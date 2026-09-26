# 技術の知見

採用技術・ライブラリ・環境固有の問題で、次に調べ直さなくて済むように残すもの。

## Starlette の状態コード定数（2026-09-26 JST）

- `HTTP_422_UNPROCESSABLE_ENTITY` と `HTTP_413_REQUEST_ENTITY_TOO_LARGE` は Starlette 0.48.0 で
  `HTTP_422_UNPROCESSABLE_CONTENT` / `HTTP_413_CONTENT_TOO_LARGE` に改名され、旧名は非推奨。
  0.47.3 の `starlette/status.py` には新名が無く、0.48.0 にはあることを配布物で確かめた。
- FastAPI 0.116〜0.119 は `starlette>=0.40` しか求めない。新名を使うなら
  `starlette>=0.48` を自分で依存に書く必要がある（`backend/pyproject.toml`、`requirements.txt`）。

## Alembic の path_separator（2026-09-26 JST）

- Alembic 1.16 以降、`alembic.ini` に `path_separator` が無いと DeprecationWarning が出る。
  `path_separator = os` を置いた。古い Alembic は知らない項目を読み飛ばすので害は無い。

## 試験の警告を見るとき

- `pytest -W error::DeprecationWarning` で流すと、非推奨の出どころが失敗として並ぶ。
  普段の `pytest -q` の要約では件数しか見えない。
- 残っている1件は `fastapi.testclient` が httpx を使うことへの Starlette の警告。
  試験側だけの話で、対応には別パッケージ（httpx2）の追加が要るため見送っている。
