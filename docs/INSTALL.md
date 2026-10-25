# 導入手順書

閉域環境（インターネットに繋がらないネットワーク）への導入手順です。

最初は現場のノートPCで実データを入れてください。 サーバー構築を先にすると、
触る前に話が止まります。1台完結で「使える」と分かってから、共有用のサーバーへ載せます。

GitHub / Git は開発側だけで使います。 導入先には持ち込み用の1ファイルだけを渡すので、
閉域側でネットに繋ぐ必要はありません。

```
[ネットに繋がる端末]                    [閉域環境]
  リポジトリを取得
  make-bundle.sh を実行
      ↓
  netvision-YYYYMMDD.tar.gz  ──USB等──▶  展開して ./install.sh
                                              ↓
                                          ./start.sh
```

---

## 1. 持ち込みファイルを作る（ネット側）

閉域側と同じ OS・同じ Python バージョンの端末で作ってください。
違うと、閉域側で入らない wheel が混ざります（例: Linux で作って Windows に持ち込む、など）。

必要なもの: Python 3.11 以上、Node.js 22 以上

```bash
git clone https://github.com/Maguro-JP/NetVision.git
cd NetVision
./packaging/make-bundle.sh
```

`dist/netvision-YYYYMMDD.tar.gz`（20MB 程度）ができます。中身は次のとおりです。

| 中身 | 内容 |
|---|---|
| `backend/` | アプリ本体のソース |
| `frontend/dist/` | ビルド済みの画面（Node.js は閉域側で不要） |
| `wheels/` | Python の依存を wheel で同梱。アプリ本体も wheel 化済み |
| `install.sh` / `install.bat` | 導入スクリプト |
| `start.sh` / `start.bat` | 起動スクリプト |
| `netvision.service` | 常時起動させる場合の systemd 設定例 |
| `docs/` | この手順書と運用手順書 |
| `VERSION` | 版数と、どの環境で作ったか |

> アプリ本体を wheel に固めてあるのが要点です。ソースのまま持ち込むと閉域側でビルドが走り、
> pip が setuptools を取りに行って失敗します。

## 2. 閉域環境へ持ち込む

USB メモリなど、その現場で許可されている方法で `netvision-YYYYMMDD.tar.gz` を渡します。

持ち込み時のチェック（ウイルスチェック等）は現場のルールに従ってください。

## 3. 導入する（閉域側）

必要なもの: Python 3.11 以上だけ（Node.js もインターネットも不要）

### Linux / macOS

```bash
tar xzf netvision-20260808.tar.gz
cd netvision-20260808
./install.sh
```

### Windows

1. `netvision-20260808.zip` を展開
2. `install.bat` をダブルクリック

Python が入っていない場合は、先に python.org のインストーラ（オフライン版）を
一緒に持ち込んでください。「Add python.exe to PATH」に必ずチェックを入れます。

## 4. 起動する

### Linux / macOS

```bash
./start.sh
```

### Windows

`start.bat` をダブルクリック

どちらも NetVision のウィンドウが開きます。ブラウザは使いません。
ウィンドウを閉じると、裏で動いていたサーバーも止まります。
起動の記録はデータの置き場所（既定 `~/.netvision`）の `launcher.log` に残ります。

> `index.html` を直接ダブルクリックしても動きません（画面だけでは動かず、
> 起動したプログラムとやりとりする必要があるためです）。

> ウィンドウは pywebview で開きます。Windows は WebView2（Windows 11 と最近の
> Windows 10 には入っています）、macOS は標準の WebKit を使います。
> Linux では GTK（`python3-gi` と `gir1.2-webkit2-4.1`）か Qt が別に要ります。

| オプション | 意味 |
|---|---|
| `--data-dir <パス>` | データの置き場所（既定 `~/.netvision`） |
| `--port 9000` | ポートを変える |

## 5. 常時起動させる（任意）

サーバーに置いて動かし続ける場合は systemd を使います。

```bash
sudo useradd --system --home /var/lib/netvision --create-home netvision
sudo cp -r netvision-20260808 /opt/netvision
sudo chown -R netvision: /opt/netvision /var/lib/netvision
sudo cp /opt/netvision/netvision.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now netvision
```

---

## 何もインストールできない環境の場合

「ソフトのインストールが禁止」「Python すら入っていない」「管理者権限が無い」——
実際によくあります。段階に応じて選んでください。

### ① Python は入っているが、pip でのインストールが禁止

前述の通常バンドルで問題ありません。仮想環境をフォルダ内に作るだけで、
システムの Python には一切触れません。

### ② Python も入っていない / 入れられない（Windows）

インストール不要版を使います。python.org が配布している embeddable package
（インストーラではなく ZIP）を同梱するので、展開するだけで動きます。

> 画面の操作から順に書いた手順書があります。GitHub もコマンド操作も初めてなら
> [WINDOWS_BEGINNER.md](WINDOWS_BEGINNER.md) の方が迷いません。ここは要点だけです。

```bash
./packaging/make-bundle-windows.sh     # ネット側で実行
# → dist/NetVision-YYYYMMDD-windows.zip（約 60MB）
```

作る側に必要なもの: Git（Windows なら Git for Windows の Git Bash）、Node.js、Python 3.11 以上。
このスクリプトは bash 用なので、Windows では Git Bash から実行します。

導入先での作業は次だけです。

1. ZIP を書き込める場所（デスクトップなど）に展開する
   （ZIP を開いた中から直接起動しても動きません）
2. `NetVision.bat` をダブルクリックする
3. NetVision のウィンドウが開く（閉じると止まる）

この方式が触らないもの（申請時の説明材料になります）

| 項目 | 内容 |
|---|---|
| インストーラ | 使わない |
| 管理者権限 | 不要 |
| レジストリ | 変更しない |
| Program Files / システムフォルダ | 触らない |
| 環境変数（システム） | 変更しない |
| サービス登録 | しない |
| 外部通信 | 一切行わない |
| 書き込み先 | 展開したフォルダと `%USERPROFILE%\.netvision` のみ |
| 待ち受け | `127.0.0.1` のみ（他端末からは見えない） |

アンインストールはフォルダを削除するだけです。

### ③ 実行ファイル（.bat / .exe）の実行自体が禁止

AppLocker や WDAC で未署名の実行ファイルが止められている場合、
クライアント端末で動かす方法はありません。正直に言うと、ここは打つ手がありません。

この場合はサーバー側に立てて、ブラウザから使う形に切り替えてください。
もともと Web アプリなので、こちらの方が本来の姿です。

- インフラ担当が管理するサーバーへ導入する（承認経路が別で、通りやすいことが多い）
- 利用者はブラウザで開くだけ。端末には何も入らない
- 利用者に伝えるのは URL 1つだけです（例: `http://netvision.example.local:8000`）。
  サーバーのIPでも構いません（例: `http://192.168.10.50:8000`）
- ただし認証が必須になります（現状は未実装。[OPERATIONS.md](OPERATIONS.md) の「いま無いもの」参照）

### ④ そもそも持ち込み自体に申請が必要

多くの組織ではこれが実態です。申請に必要そうな情報をまとめておきます。

| 聞かれること | 回答 |
|---|---|
| 何をするソフトか | ネットワーク機器の台帳・保守期限・構成を管理する社内ツール |
| 通信するか | 外部通信は一切なし。ローカルのみ |
| 何を保存するか | 機器の型番・バージョン・構成情報。保存先はローカルのファイル1つ |
| 外部送信するか | しない。クラウド連携も無い |
| 開発元 | 自社（ソースコードを提示できる） |
| 依存する外部部品 | Python と OSS ライブラリ（一覧は `lib/` の中身で提示可能） |

ソースを全部見せられるのが強みなので、中身を開示する前提で話を通すのが早いと思います。

---

## 待ち受けアドレスについて（重要）

既定では `127.0.0.1`（自分の端末）からしか開けません。

このツールにはまだ認証機能がありません。 実機の Config には管理IP・ACL・
SNMP コミュニティ名などが入るため、URL を知っている人が全部見られる状態は避けるべきです。

他の人にも使わせる段階になったら、認証を実装してから `--host 0.0.0.0` にしてください。
どうしても先に共有が必要な場合は、最低限これらを検討してください。

- リバースプロキシ（nginx / Apache）で Basic 認証をかける
- アクセス元をファイアウォールで限定する
- 運用端末からの SSH ポートフォワードで繋ぐ

## 複数人で使う場合（Docker Compose）

Postgres を使う本番相当の構成です。閉域へ持ち込む場合はイメージを固めて渡します。

```bash
# ネット側
docker compose build
docker save netvision-backend netvision-frontend postgres:16-alpine \
  | gzip > netvision-images.tar.gz

# 閉域側
gunzip -c netvision-images.tar.gz | docker load
docker compose up -d
```

画面は <http://127.0.0.1:8080>。この構成でもマイグレーションは起動時に自動適用されます。

---

## 動作確認

導入後、次が通れば正常です。

```bash
curl http://127.0.0.1:8000/health
# => {"status":"ok"}

curl http://127.0.0.1:8000/api/v1/meta
# => {"demo_mode":false,...}
```

ブラウザで開いて、左のナビが表示され「機器一覧」が空で出れば導入完了です。

## うまくいかないとき

| 症状 | 原因と対処 |
|---|---|
| `python3 が見つかりません` | Python 3.11 以上を入れる。Windows は PATH 追加を忘れずに |
| `Python 3.11 以上が必要です` | 古い Python が使われている。`python3 -V` を確認 |
| wheel が見つからないエラー | バンドルを作った OS / Python が閉域側と違う。同じ環境で作り直す |
| ポートが使用中 | `--port 9000` など空いている番号にする |
| 画面は出るがデータが出ない | `/api/v1/meta` を直接開いて応答を確認。エラー内容を控える |


## 時刻の表示

DB には UTC で保存し、画面と書き出しには使う人の時刻で出します。既定は日本時間
（`Asia/Tokyo`）です。サーバーの時計が UTC でも、資料に載る「◯◯時点」は日本時間になります。

別の地域で使う場合は環境変数で変えられます。

```bash
NETVISION_DISPLAY_TIMEZONE=Asia/Tokyo   # 既定
```

画面上の日時はブラウザの時刻に合わせて表示されます（API はタイムゾーン付きで返します）。
