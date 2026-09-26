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

## 画面を全ページ開いて確かめる（2026-09-26 JST）

- 一時 DB に `alembic upgrade head` と `python -m scripts.seed` で見本（57拠点・120台）を入れ、
  `NETVISION_SERVE_FRONTEND=../frontend/dist` で backend から画面を配る。
- Chromium は `/opt/pw-browsers/chromium-1194/chrome-linux/chrome`。playwright-core は
  リポジトリに入れず scratchpad に入れて使う。
- `frontend/src/nav.ts` の経路と機器・シナリオの詳細の 32 ページを開き、console のエラー、
  画面の例外、5xx の応答を集めた。2026-09-26 時点で 0 件。

## 取り込みの速さ（2026-09-26 JST）

- 5000 行の CSV（管理ID・シリアル番号・拠点あり）を SQLite で: 新規 1.1 秒、全行更新 1.3 秒。
- 重複の確認で機器を関連ごと読むと、全行更新が 1.7 秒になった。値と id だけを引けば変わらない。
  取り込みに確認を足すときは、同じ 5000 行で前後を測る。

## 2 万台での応答時間（2026-09-26 JST、SQLite、手元）

| API | 時間 |
|---|---|
| 機器一覧 50 件 / 検索つき | 0.12 / 0.17 秒 |
| 概況（summary） | 0.23 秒 |
| 指摘（insights） | 0.37 秒 |
| 更改計画（replacement/plan） | 0.58 秒 |
| 構成図（2 万台の台帳、結線なし） | 0.08 秒 |
| 台帳の CSV 書き出し | 3.9 秒 → 設定の読み直しをやめて約 1.9 秒 |
| 台帳の Excel 書き出し | 10 秒（直す前。openpyxl の分が大きい） |
| `/alerts`（画面からは呼ばない） | 2.1 秒（上限なしで全件返す） |

- 測り方: 一時 DB に alembic で表を作り、拠点 500・機器 2 万・契約 1 万を直接入れて uvicorn で叩いた。
- 設定ファイル（settings.json）の読み直しが、機器ごとの判定で 5 万回起きていた。`appsettings.load` は
  更新時刻と大きさで覚える。
