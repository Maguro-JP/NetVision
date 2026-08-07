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
- **既存台帳の取り込み** — Excel / CSV をそのまま読み込み、列名を自動で対応付け（後述）
- **台帳の書き出し** — Excel / CSV で出力。書き出したファイルはそのまま取り込み直せる（後述）
- **変更履歴** — 機器・保守契約・拠点への変更をすべて自動記録。誰がいつ何をどう変えたかを追える
- **最適化の指摘** — 払わなくてよい保守費とリスクを洗い出し、年間削減見込みを算出
- **将来構成シナリオ** — 台帳に触れずに「入れ替えたらどうなるか」を試算し、現状と比較
- **Config の取り込み** — running-config / show 出力から構成情報を自動抽出、世代管理と差分表示
- **トポロジ図** — LLDP / CDP から結線を自動推定し、人が配置を整えて仕上げる図エディタ
- **リファレンス管理** — EoL 情報を型番単位で管理。出典と取得日を必ず記録し、鮮度を可視化
- **脆弱性の突合** — CVE と機器のバージョンを突き合わせ、悪用確認済み（CISA KEV）を最優先で提示
- **ベンダードキュメント** — コマンドリファレンスやマニュアルを、ベンダー・型番・バージョン範囲で管理。**古い版は消さない**
- **Config テンプレート** — 機器を選ぶとその書式に合ったテンプレートで設定変更用 Config を生成（実機には投入しない）

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
    services/    ライフサイクル判定・取り込み・書き出し・変更履歴・指摘・シナリオ
      configparse/ ベンダー別 Config パーサ（Cisco / Juniper / Fortinet）
      reference.py リファレンス取り込みと CVE 突合
      documents.py ベンダー資料の保管と適用判定
      config_render.py Config テンプレートの展開（サンドボックス）
      versions.py  OSバージョンの自然順比較
    api/routes/  エンドポイント
  alembic/       DB マイグレーション
  scripts/seed.py  デモデータ投入
  tests/         pytest
frontend/
  src/pages/     画面（ダッシュボード / トポロジ図 / 機器一覧 / 詳細 / 拠点 /
                 Config取り込み / 台帳取り込み / リファレンス管理 /
                 ベンダードキュメント / Configテンプレート /
                 脆弱性 / 最適化の指摘 / 将来構成シナリオ / 変更履歴）
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

## 既存台帳の取り込み

ゼロから入力し直す前提を置かず、**いま使っている Excel / CSV 台帳をそのまま読み込む**ことを
移行の起点にしています。画面は「台帳の取り込み」から。

対応形式は `.csv` / `.tsv` / `.xlsx`。CSV は Shift_JIS（CP932）・BOM 付き UTF-8 のどちらでも読めます。

### 仕組み

1. **列名の自動対応付け** — 「機器名」「メーカー」「型式」「導入年月日」「保守期限」のような
   現場でよく使われる表記を別名辞書で解決します。全角/半角・大文字小文字・記号の揺れも吸収します。
2. **判別できない列は画面で指定** — 自動判別を上書きしたり、取り込まない列を選べます。
3. **必ずプレビューしてから取り込む** — 行ごとに「新規 / 更新 / スキップ / エラー」と理由を表示します。
4. **エラーが1行でもあれば取り込まない** — 中途半端に一部だけ入る状態を作りません。
5. **エラー行だけ書き出して直せる** — エラーになった行を、元の列のまま
   「エラー内容」列を付けて CSV に出力します。Excel で直してそのまま取り込み直せます。

### 取り込み時の扱い

| 項目 | 挙動 |
|---|---|
| 既存機器の判定 | ホスト名（既定）またはシリアル番号で突き合わせ。選択可 |
| 既存機器の更新 | **値が入っている列だけ**反映。台帳の空欄で既存値を消さない |
| 拠点 | 台帳の拠点名から自動作成し、機器に紐付け |
| 保守契約 | 保守終了日の列があれば契約を作成。同じ契約は重複登録しない |
| 日付 | `2024/4/1` `2024-04-01` `2024年4月1日` `2024/04` などを解釈 |
| 金額 | `¥480,000` `480000円` のような表記を解釈 |
| 種別・状態 | `L3SW` `FW` `ルータ` `稼動中` `撤去済み` などの日本語表記を解釈 |

記入用テンプレート CSV は画面からダウンロードできます（`GET /api/v1/import/template.csv`、
Excel でそのまま開けるよう BOM 付き UTF-8）。

## 台帳の書き出し

機器一覧の絞り込み条件をそのまま引き継いで、Excel / CSV に書き出せます。

| 形式 | 用途 |
|---|---|
| Excel (.xlsx) | 日付は日付セル・金額は数値セルで出力。見出し固定とオートフィルタ付き |
| CSV (UTF-8) | BOM 付き。Excel で開いても文字化けしない |
| CSV (Shift_JIS) | 古い Excel や既存ツールに渡す場合 |
| 取り込み用CSV | 算出列を除いた、取り込める列だけのCSV |

**書き出したファイルは、そのまま取り込み直せます。** 出力の列見出しは取り込み側の項目名と
一致させてあるため、次の運用が回ります。

```
書き出し → Excel で一括編集 → 取り込み
```

これにより、最初の1回さえ取り込めば、以降はフォーマットの揺れを気にせず運用できます。
往復して内容が変わらないことは自動テストで担保しています（`tests/test_export_api.py`）。

算出列（保守期限・残日数・経過年数・アラート）も既定で出力されます。これらは取り込み対象では
ないため、取り込み時は自動的に「取り込まない」列として扱われます。稟議・見積依頼用の資料には
そのまま使えます。

## 変更履歴

機器・保守契約・拠点への変更は**すべて自動で記録されます**。画面での編集も台帳の取り込みも、
同じ履歴に残ります。

- 更新は**項目ごとに「変更前 → 変更後」**を記録します
- 対象が削除された後も、何を削除したかが履歴から読めます
- 取り込み1回分の変更は同じバッチIDでまとまるため、「あの取り込みで何が変わったか」を追えます
- 発生元（画面操作 / 台帳の取り込み / デモデータ）で絞り込めます

実装は SQLAlchemy のフラッシュに介入する方式（`app/services/audit.py`）です。
個々のAPIに記録処理を書くと必ず抜けが出るため、**1か所で全ての変更を捕捉**しています。

## 最適化の指摘

登録済みの台帳から、外部データを使わずに判定できる「無駄」と「リスク」を洗い出します。
削減できる年額の見込みも出すので、契約見直しや更改の稟議の根拠に使えます。

| 分類 | 指摘の例 |
|---|---|
| 無駄をなくす | 撤去済み機器に保守契約が残っている / 予備機に稼働機と同等の保守 / 契約期間の重複 / EoL より先まで伸びた契約 |
| リスクを減らす | サポート終了済みなのに稼働中 / 稼働中なのに有効な保守契約がない / 導入から7年超 |
| 台帳を整える | EoL も保守期限も未登録 / 設置拠点が未登録 |
| 契約をまとめる | 同じ型番なのに保守ベンダーが分かれている |

## 将来構成シナリオ

「この機器を入れ替えたらどうなるか」を、**実際の台帳に一切触れずに**試算します。

- 「期限が近い機器から入れ替え案のたたき台を作る」で、ゼロから並べる手間を省けます
- 入れ替え / 新規追加 / 撤去 / 据え置き を並べ、台数・単価・保守年額・想定EoLを入力
- 現状と適用後を同じ指標（台数・年間保守費・サポート切れ台数・平均経過年数）で比較
- 初期投資と年間保守費の増減を算出
- 評価時点（`target_date`）を将来に置けば、「その時点で何台が期限切れになっているか」も見られます
- 検討中 / 稟議中 / 承認済み / 見送り の状態を持てます

対象の機器が台帳から削除されても案は残り（何を検討していたかは名前で残る）、
比較時に警告が出ます。

## Config の取り込み

running-config や show 出力を読み込み、**機器情報・インターフェース・VLAN・隣接情報**を抽出します。
Cisco IOS / IOS-XE / NX-OS、Juniper Junos、Fortinet FortiOS に対応（ベンダーは自動判別）。

`show version` `show cdp neighbors detail` などを1ファイルにまとめたものでも読めます。
CSV と同じく Shift_JIS / UTF-8 のどちらでも構いません。

### 抽出するもの

| 種別 | 内容 |
|---|---|
| 機器情報 | ホスト名、型番、シリアル、OS名、OSバージョン |
| インターフェース | 名前、description、IPアドレス、VLAN、access/trunk、shutdown 状態 |
| VLAN | ID と名前 |
| 隣接情報 | LLDP / CDP のネイバー（detail 形式・表形式の両方） |

### 台帳との関係（重要）

**Config から読み取った値で台帳を自動上書きしません。** 実機と台帳が食い違っていること自体が
情報だからです。取り込み時は差分を提示するだけで、反映するかどうかは人が選びます。

さらに、`running-config` の `version 15.2` のように**Config 側の情報が粗いだけ**の場合は
差分として扱いません（適用しても台帳の `15.2(7)E6` を劣化させないため）。

Config の原文は世代ごとに保存され、前の世代との差分を表示できます。同じ内容を再取り込みしても
世代は増えません（sha256 で判定）。変更追跡・監査の証跡として使えます。

## トポロジ図

**Config を取り込むだけで、結線が自動で組み上がります。** LLDP / CDP のネイバー情報から
「どの機器のどのポートが、どの機器のどのポートに繋がっているか」を推定します。

- `Gi1/0/2` と `GigabitEthernet1/0/2`、`sw2.example.com` と `sw2` のような表記ゆれは吸収します
- **両側の機器から同じ結線が見えたものは「確認済み」**として区別します（実線 / 点線）
- 台帳に無い機器と繋がっている場合は、記載漏れの可能性として報告します
- 機器が後から登録された場合は「Config から結線を作り直す」で再推定できます

図の上では次のことができます。

- ノードをドラッグして配置を整える（**配置は保存され、機器が増えても崩れません**）
- ノード同士をつないで**手で線を引く**（自動再推定しても手で引いた線は消えません）
- 線を選んで削除、レイヤ（物理 / L2 / L3 / 論理）ごとに別の配置を持つ
- ノードの色は保守期限・EoL のアラート状態と連動します

## リファレンス管理（EoL / 脆弱性）

**外部への通信は一切行いません。** 閉域環境での運用を前提に、
オフラインでの持ち込みを唯一かつ第一級の経路にしています。

### EoL は型番に紐づける

EoL は本来「型番の属性」です。機器ごとに持たせると同じ日付を何度も入力することになり、
ベンダーが訂正したときに全台直す羽目になります。そこで製品マスタに正規化しています。

```
製品リファレンス（ベンダー + 型番）
  ├ EoS / EoL / ソフトウェア保守終了 / 最終サポート日 / 推奨後継機
  ├ 出典（手入力 / CSV / NVD / KEV / ベンダー資料）
  ├ 出典URL
  └ 情報取得日   ← 「いつ時点の情報か」を必ず持つ
        ↓ ベンダー+型番で自動解決（大文字小文字の揺れは吸収）
機器（個別の事情があれば機器側の値が優先される）
```

これにより「C9500-40X の EoL が変わった」を1回の更新で該当機器すべてに反映できます。
機器詳細には EoL が製品マスタ由来か機器個別の入力かを表示します。

### 鮮度の可視化

リファレンスは放っておけば古くなります。自動更新されない以上、
**いつ時点の情報で判断しているか**を運用者が意識できないと使えません。

- 取得日から `NETVISION_REFERENCE_STALE_DAYS`（既定180日）を過ぎたら「要更新」を表示
- リファレンスが未登録の型番を一覧表示（その機器は EoL を判定できない）
- 最も古い取得日をダッシュボードに表示

### 脆弱性データの取り込み

JSON を置くだけで、形式は中身から自動判別します。

| 形式 | 入手元 | 備考 |
|---|---|---|
| NVD 2.0 | NIST NVD | CPE からバージョン範囲を組み立てる |
| CISA KEV | CISA | **実際に悪用が確認された**脆弱性。優先度判断で最も効く |
| 簡易形式 | ベンダー PSIRT を手で整理 | 型番のワイルドカード指定（`C9500*`）に対応 |

突合はベンダー・型番・OSバージョンで行い、**何で一致したかを必ず表示**します
（「バージョン 17.09.04a が影響範囲に含まれる」）。根拠を示せない判定は運用で使えないためです。

バージョン比較は semver ではなく自然順比較で、`17.09.04` と `17.9.4`、
`15.2(7)E6`、`21.4R3-S5.4` のような表記を正しく大小比較できます。

OSバージョンが未登録の機器は**判定できない**ため、その台数を明示します（見落とし防止）。

### 自動取得について

公式ソースの自動取得はベンダーによって難易度が大きく異なります（Cisco EoX API と
NVD / CISA KEV は API があり、Juniper・Fortinet は実質手動）。
現状は全ベンダーを同じ手順で扱えるオフライン取り込みに統一しています。
オンライン同期を足す場合も、この取り込み経路の上にアダプタとして載る形になります。

## ベンダードキュメント（コマンドリファレンス / マニュアル）

コマンドの書式はメーカー・型番・バージョンで変わります。資料も同じ3軸で管理します。

```
資料（ベンダー ＋ 型番 ＋ 適用バージョン範囲）
  ├ 種別（コマンドリファレンス / コンフィグガイド / リリースノート / …）
  ├ 適用バージョン  17.9.0 〜 17.9.99（範囲。空なら全バージョン共通）
  ├ 実体ファイル（PDF など。省略可、URL だけの登録もできる）
  └ 後継資料へのリンク（superseded_by）
        ↓ ベンダー + 型番 + 稼働中バージョンで自動解決
機器  →「この機器で使える資料」を逆引き
```

### 古い版を消さない（設計上の約束）

現場によっては古いバージョンが現役です。新しい版を登録しても、**旧版は削除されません**。

- 新版を登録したら「後継を指定」でリンクするだけ。旧版の行はそのまま残る
- 一覧は既定で旧版も表示（`include_superseded=false` で現行のみ）
- 17.9 で動いている機器からは、17.12 の版が出た後も 17.9 向けの資料が引かれる
- 明示的な削除 API はあるが、UI では「版が新しくなっただけなら後継指定を使ってください」と確認する

型番は空欄でベンダー全体、`C9500*` で前方一致（シリーズ共通資料）を表せます。

### ファイルの保管

実体は DB ではなくファイル領域（`NETVISION_DOCUMENT_STORAGE_PATH`）に置き、
内容の SHA-256 でパスを決めます。同じファイルを2回登録しても実体は1つで、
片方を消してももう片方が参照している限りファイルは残ります。

> **バックアップは2系統必要です。** DB（メタデータ）とファイル領域（実体）の両方を取ってください。
> Docker Compose では `documents` ボリュームが実体の置き場です。

### 自動取得について

閉域運用が前提のため、**アプリからの外部通信は一切行いません**。
資料は手動アップロード（またはURLの記録のみ）で登録します。
入手元URLと取得日を残せるので、後から「どこから持ってきた版か」を追えます。

## Config テンプレート

設定変更用の Config を、機器に合った書式で生成します。

- テンプレートは資料と同じ3軸（ベンダー / 型番 / バージョン範囲）で管理
- 機器を選ぶと、そのバージョンに合うテンプレートだけが候補に出る
- 根拠にしたマニュアル（`source_document_id`）を紐付けられる
- 本文は Jinja2 記法。`{{ vlan_id }}` のような変数に加え、`device.hostname` や
  `interfaces` ループで台帳・Config 取り込み済みの値をそのまま差し込める

```
vlan {{ vlan_id }}
 name {{ vlan_name }}
!
interface {{ interface }}
 description {{ vlan_name }} ({{ device.hostname }})
 switchport mode access
 switchport access vlan {{ vlan_id }}
```

### 事故を防ぐための作り

- **生成するだけで、実機には一切投入しません。** 内容を確認して手で流し込む前提です
- 未定義の変数は空文字にせず**エラーにします**（`{{ vlna_id }}` のような打ち間違いで
  壊れた config が出ると気付けないため）
- 必須変数が埋まっていなければ「必須の変数「VLAN 名」が入力されていません」と明示
- 適用バージョン範囲から外れた機器に当てた場合は生成しつつ**警告を出します**
  （現場判断で流用したい場面があるため、禁止ではなく警告）
- テンプレートはサンドボックス（`SandboxedEnvironment`）で展開し、内部構造には触れられません

## 設定（環境変数）

| 変数 | 既定値 | 説明 |
|---|---|---|
| `NETVISION_DATABASE_URL` | `postgresql+psycopg://netvision:netvision@localhost:5432/netvision` | 接続先DB |
| `NETVISION_CRITICAL_DAYS` | `90` | この日数以内の期限を `critical` とする |
| `NETVISION_WARNING_DAYS` | `365` | この日数以内の期限を `warning` とする |
| `NETVISION_REFERENCE_STALE_DAYS` | `180` | リファレンスがこの日数より古ければ「要更新」とする |
| `NETVISION_DOCUMENT_STORAGE_PATH` | `./data/documents` | 資料ファイルの保管先（DB とは別にバックアップが必要） |
| `NETVISION_MAX_DOCUMENT_BYTES` | `209715200`（200MB） | 1ファイルあたりの上限 |
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
| POST | `/import/devices/preview` | 台帳の取り込み内容を検証（DB は変更しない） |
| POST | `/import/devices` | 台帳の取り込みを実行 |
| GET | `/import/fields` | 取り込み先として指定できる項目の一覧 |
| GET | `/import/template.csv` | 記入用テンプレートCSV |
| GET | `/export/devices.csv` | 台帳をCSVで書き出し（`encoding=utf-8\|shift_jis`、`include_status`） |
| GET | `/export/devices.xlsx` | 台帳をExcelで書き出し |
| POST | `/import/devices/errors.csv` | エラー行だけを元の列のまま書き出し |
| GET | `/changes` | 変更履歴（`entity_type` / `entity_id` / `action` / `source` / `batch_id` / `q` / `since` で絞り込み） |
| GET | `/insights` | 最適化の指摘（`category` / `site_id` で絞り込み） |
| GET / POST | `/scenarios` | 将来構成シナリオの一覧・作成 |
| GET / PATCH / DELETE | `/scenarios/{id}` | シナリオの取得・更新・削除 |
| GET | `/scenarios/{id}/summary` | 現状と適用後の比較 |
| POST | `/scenarios/{id}/generate` | 期限が近い機器からたたき台を生成 |
| POST | `/scenarios/{id}/items` | シナリオ項目の追加 |
| PATCH / DELETE | `/scenarios/items/{id}` | シナリオ項目の更新・削除 |
| POST | `/configs/upload` | Config を取り込み（複数可、`apply_inventory` で台帳へ反映） |
| POST | `/configs/preview` | 保存せずに読み取り内容だけ確認 |
| GET | `/configs` | 取り込み履歴 |
| GET | `/configs/{id}/diff` | Config の世代間差分 |
| GET | `/configs/devices/{id}/interfaces` | 抽出したインターフェース一覧 |
| GET | `/topology` | 図のノードと結線（`layer` 指定可） |
| GET | `/topology/stats` | 結線の統計と未接続機器 |
| PUT / DELETE | `/topology/positions` | ノード配置の保存・リセット |
| POST | `/topology/rebuild` | Config から結線を再推定 |
| POST / PATCH / DELETE | `/topology/links` | 手動での結線の追加・更新・削除 |
| GET / POST | `/reference/products` | 型番リファレンスの一覧・登録 |
| PATCH / DELETE | `/reference/products/{id}` | 型番リファレンスの更新・削除 |
| POST | `/reference/products/import` | 型番リファレンスの CSV / Excel 取り込み |
| GET | `/reference/products/template.csv` | 記入用テンプレート |
| POST | `/reference/vulnerabilities/import` | 脆弱性 JSON の取り込み（形式自動判別） |
| GET | `/reference/vulnerabilities` | CVE 一覧（`affecting_only` で自社該当のみ） |
| GET | `/reference/vulnerabilities/matches/summary` | 機器と CVE の突合結果 |
| GET | `/reference/freshness` | リファレンスの鮮度 |
| GET / POST | `/documents` | 資料の一覧・登録（`q` / `vendor` / `doc_type` / `include_superseded`） |
| PATCH / DELETE | `/documents/{id}` | 資料の更新・削除 |
| POST | `/documents/{id}/supersede/{successor_id}` | 後継資料を指定（旧版は残る） |
| GET | `/documents/{id}/download` | 資料ファイルのダウンロード |
| GET | `/devices/{id}/documents` | その機器で使える資料（該当理由つき） |
| GET / POST | `/config-templates` | Config テンプレートの一覧・作成 |
| PATCH / DELETE | `/config-templates/{id}` | テンプレートの更新・削除 |
| POST | `/config-templates/{id}/render` | Config を生成（実機には投入しない） |
| GET | `/devices/{id}/config-templates` | その機器に使えるテンプレート |
| GET / POST | `/sites` | 拠点の一覧・登録 |
| PATCH / DELETE | `/sites/{id}` | 拠点の更新・削除 |

## テスト

```bash
cd backend  && .venv/bin/pytest && .venv/bin/ruff check .
cd frontend && npm test && npm run typecheck
```

## 今後

更改計画のトポロジ上での影響範囲表示、図のエクスポート、Config の自動収集を予定しています。
詳細は [docs/ROADMAP.md](docs/ROADMAP.md) を参照してください。
