# NetVision

ネットワーク機器の**インベントリ・保守期限・ライフサイクル**をまとめて管理するツールです。

「この機器はいつ保守が切れるのか」「どの型番がサポート終了を迎えるのか」を
Excel 台帳ではなく、常に最新の状態で見られるようにすることを目的にしています。

## できること（現時点）

- **機器インベントリ** — ベンダー / 型番 / シリアル / OS バージョン / 導入日 / 設置場所（拠点・ラック・U）を管理
- **保守契約管理** — 1台に複数の契約を紐付け、最も遠い終了日を「保守期限」として自動判定
- **ライフサイクル判定** — 保守期限・EoS（販売終了）・EoL（サポート終了）の残日数を計算し、重要度を色分け
  - `expired` 期限切れ / `critical` 90日以内 / `warning` 365日以内 / `ok` / `unknown` 情報未登録
  - しきい値は環境変数で変更可能
- **ダッシュボード** — 重要度別の台数、期限が近い機器、ベンダー別台数を一覧
- **更改計画向けの抽出** — 「今後 N 日以内に期限を迎える機器」を期限が近い順に取得

`unknown`（情報未登録）を `ok` より重い扱いにしているのは、台帳の抜けを見逃さないためです。

## 構成

| 層 | 技術 |
|---|---|
| バックエンド | Python 3.11+ / FastAPI / SQLAlchemy 2.0 / Alembic |
| データベース | PostgreSQL 16 |
| フロントエンド | TypeScript / React 18 / Vite / TanStack Query / React Router |
| 配布 | Docker Compose（閉域環境でのオンプレ運用を想定） |

```
backend/
  app/
    models/      SQLAlchemy モデル（Device / MaintenanceContract / Site）
    schemas/     Pydantic スキーマ（API の入出力）
    services/    ライフサイクル判定ロジック
    api/routes/  エンドポイント
  alembic/       DB マイグレーション
  scripts/seed.py  デモデータ投入
  tests/         pytest
frontend/
  src/pages/     画面（ダッシュボード / 機器一覧 / 機器詳細 / 機器フォーム / 拠点）
  src/api/       API クライアントと型定義
  src/lib/       表示フォーマット
docs/ROADMAP.md  今後の開発計画
```

## 起動方法

### Docker Compose（推奨）

```bash
docker compose up --build
```

- 画面: http://localhost:8080
- API ドキュメント（Swagger UI）: http://localhost:8000/docs

デモデータを入れる場合:

```bash
docker compose exec backend python -m scripts.seed
```

### ローカル開発

バックエンド:

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
export NETVISION_DATABASE_URL="postgresql+psycopg://netvision:netvision@localhost:5432/netvision"
.venv/bin/alembic upgrade head
.venv/bin/python -m scripts.seed          # 任意
.venv/bin/uvicorn app.main:app --reload
```

フロントエンド（`/api` は自動で `localhost:8000` にプロキシされます）:

```bash
cd frontend
npm install
npm run dev        # http://localhost:5173
```

## 設定（環境変数）

| 変数 | 既定値 | 説明 |
|---|---|---|
| `NETVISION_DATABASE_URL` | `postgresql+psycopg://netvision:netvision@localhost:5432/netvision` | 接続先DB |
| `NETVISION_CRITICAL_DAYS` | `90` | この日数以内の期限を `critical` とする |
| `NETVISION_WARNING_DAYS` | `365` | この日数以内の期限を `warning` とする |
| `NETVISION_CORS_ORIGINS` | `["http://localhost:5173","http://localhost:3000"]` | CORS 許可オリジン（JSON配列） |

## 主なAPI

ベースパス `/api/v1`

| メソッド | パス | 説明 |
|---|---|---|
| GET | `/summary` | ダッシュボード用サマリ |
| GET | `/alerts?within_days=365` | 期限が近い機器を近い順に取得 |
| GET | `/devices` | 機器一覧（`q` / `vendor` / `device_type` / `status` / `site_id` / `severity` で絞り込み） |
| POST | `/devices` | 機器登録 |
| GET / PATCH / DELETE | `/devices/{id}` | 機器の取得・部分更新・削除 |
| GET / POST | `/devices/{id}/contracts` | 保守契約の一覧・登録 |
| PATCH / DELETE | `/contracts/{id}` | 保守契約の更新・削除 |
| GET / POST | `/sites` | 拠点の一覧・登録 |
| PATCH / DELETE | `/sites/{id}` | 拠点の更新・削除 |

## テスト

```bash
cd backend  && .venv/bin/pytest && .venv/bin/ruff check .
cd frontend && npm test && npm run typecheck
```

## 今後

Config 取り込みによる構成情報の自動抽出、LLDP/CDP からのトポロジ自動生成、
図エディタでの可視化、CVE 突合を予定しています。詳細は [docs/ROADMAP.md](docs/ROADMAP.md) を参照してください。
