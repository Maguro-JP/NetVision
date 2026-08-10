# 版を上げる手順

新しい版に入れ替えるときの手順です。閉域に置いてある前提で書いています。

考え方は1つだけです。**データは持ち物、アプリは入れ替え物**。
アプリのフォルダは丸ごと差し替えてよく、データディレクトリには触りません。
だからこそ、入れ替える前にデータを取っておけば必ず戻せます。

---

## 上げる前に

### 1. いまの版を控える

画面が動く状態で、ブラウザのアドレス欄に次を入れます。

```
http://127.0.0.1:8000/api/v1/meta
```

こういう内容が出ます。

```json
{"demo_mode": false, "version": "0.1.0", "schema_revision": "d5e2b71fa93c", ...}
```

`version` と `schema_revision` を控えてください。上げたあとに見比べます。
控えていないと「入れ替えたつもりで古いまま動いていた」に気づけません。

### 2. バックアップを取る

**ここを飛ばさないでください。** 戻せなくなるのはここを飛ばしたときだけです。

| 動かし方 | 取るもの |
|---|---|
| Windows のインストール不要版 | `C:\Users\（ユーザー名）\.netvision` フォルダをコピー |
| 1台完結モード（Linux / macOS） | `~/.netvision` ディレクトリをコピー |
| Docker Compose | DB のダンプと資料の領域（下記） |

```bash
# 1台完結モード
cp -r ~/.netvision ~/backup/netvision-$(date +%Y%m%d)

# Docker Compose
docker compose exec db pg_dump -U netvision netvision > netvision-$(date +%Y%m%d).sql
docker run --rm -v netvision_documents:/d -v "$PWD:/out" alpine \
  tar czf /out/documents-$(date +%Y%m%d).tar.gz -C /d .
```

コピーが終わったら、フォルダの大きさが元と同じくらいあるか見てください。
0バイトのバックアップを取っていた、が一番多い失敗です。

---

## 入れ替える

### Windows のインストール不要版

1. 新しい ZIP を用意する（[WINDOWS_BEGINNER.md](WINDOWS_BEGINNER.md) の第1部）
2. 動いている黒いウィンドウを**閉じて**止める
3. 新しい ZIP を、いまとは**別のフォルダ**に展開する
4. 新しいフォルダの `NetVision.bat` をダブルクリックする

古いフォルダは消さずに残しておいてください。何かあればそちらを起動すれば戻せます。
データは `.netvision` にあるので、どちらのフォルダから起動しても同じものが見えます。

問題がないことを確かめてから、古いフォルダを消します。

### 1台完結モード（Linux / macOS）

```bash
# 止める（動かしている端末で Ctrl+C）
cd ~/NetVision
git pull                       # または新しいバンドルを展開
./packaging/install.sh
./packaging/start.sh
```

### Docker Compose

```bash
docker compose down
# イメージを差し替える（新しいバンドルを展開、または新しいタグを指定）
docker compose up -d
docker compose logs -f api     # 起動しきるまで見る
```

---

## スキーマの変更について

表の形が変わることがあります。どの動かし方でも**起動時に自動で適用される**ので、
手で流す必要はありません。

- 1台完結モード … 起動時に足りない表を作ります
- Docker Compose … 起動時に Alembic のマイグレーションが走ります

自動で当たるぶん、**バックアップを取っていないと戻せません**。
古いアプリは新しいスキーマを読めないので、アプリだけ戻しても動きません。
戻すときはデータも一緒に戻します（下記）。

---

## 上げたあとの確認

上から順に見てください。ここまで通れば入れ替えは成功です。

| 見るところ | 期待 |
|---|---|
| `/api/v1/meta` | `version` が新しくなっている |
| 同上 | `schema_revision` が変わっている（変更があった版なら） |
| 機器一覧 | 台数が前と同じ |
| トポロジ図 | 拠点を選んで図が出る |
| Config の取り込み | 取り込み履歴が残っている |

台数が減っていたら、そこで止めて戻してください。先へ進むほど戻しにくくなります。

---

## 戻す

新しい版で問題が出たときは、**アプリとデータを対にして**戻します。

### Windows のインストール不要版

1. 黒いウィンドウを閉じて止める
2. `C:\Users\（ユーザー名）\.netvision` を、控えておいたフォルダで置き換える
3. 古いフォルダの `NetVision.bat` を起動する

### 1台完結モード

```bash
# 止めてから
rm -rf ~/.netvision
cp -r ~/backup/netvision-20260810 ~/.netvision
# アプリも古い版に戻してから起動する
```

### Docker Compose

```bash
docker compose down
docker compose up -d db
docker compose exec -T db psql -U netvision netvision < netvision-20260810.sql
# イメージを古いものに戻してから
docker compose up -d
```

---

## 版の付け方（開発側）

| 変えたもの | 上げるところ |
|---|---|
| 表の形（マイグレーションが増えた） | 真ん中（0.1.0 → 0.2.0） |
| 画面や動きの追加・修正だけ | 一番右（0.1.0 → 0.1.1） |

版数は `backend/pyproject.toml` と `backend/app/main.py` の2か所にあります。
食い違うと `/api/v1/meta` が嘘をつくので、必ず揃えてください。
揃っていることは試験で見ています（`backend/tests/test_meta.py`）。

スキーマを変えたときは、マイグレーションを必ず1つ足してください。
1台完結モードは表を自動で作りますが、Docker Compose 側は
マイグレーションが無いと古い形のまま動き続けます。
