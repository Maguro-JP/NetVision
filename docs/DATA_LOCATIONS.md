# データの置き場所

NetVision のデータ（台帳の DB、取り込んだ資料、設定、起動の記録）をどこに置くかの決まりです。
1台で使う形と、サーバーに置いて複数人で使う形で、置き方を分けています。

## 1台で使う（アプリのウィンドウ、Setup.exe、ZIP、start.sh）

DB は SQLite の1ファイルです。データの置き場所は、次の順で決まります。

1. 起動のときの `--data-dir <パス>`
2. 環境変数 `NETVISION_DATA_DIR`
3. 前の版の既定 `~/.netvision`（Windows は `%USERPROFILE%\.netvision`）に `netvision.db` があれば、そこ
4. OS ごとの決まりの場所

| OS | 決まりの場所 |
|---|---|
| Windows | `%LOCALAPPDATA%\NetVision`（`C:\Users\<名前>\AppData\Local\NetVision`） |
| macOS | `~/Library/Application Support/NetVision` |
| Linux | `$XDG_DATA_HOME/netvision`（無ければ `~/.local/share/netvision`） |

前の版から使っている場合は、3 のとおり `~/.netvision` をそのまま使い続けます。
データを黙って動かすと、どこにあるのか分からなくなるためです。移したいときは、控えを取ってから
フォルダごとコピーし、`NETVISION_DATA_DIR` で新しい場所を指定してください（元のフォルダは、新しい場所で
起動して中身を確かめるまで消さないでください）。

置き場所の中身は次のとおりです。

| 名前 | 中身 |
|---|---|
| `netvision.db` | 台帳・Config の原文・図の配置・履歴 |
| `documents/` | 取り込んだ資料 |
| `settings.json` | 画面で変えた設定 |
| `launcher.log` | アプリのウィンドウの起動の記録 |

AI のモデルはここに置きません。自作のアプリどうしで共有する `C:\LLM\gguf`（または `LLM_HOME`）に置きます
（[LOCAL_LLM.md](LOCAL_LLM.md)）。

### この場所にした理由

- 利用者ごとの場所にした。Setup.exe は管理者権限なしで利用者ごとに入るので、データも利用者ごとにそろえる
- Windows で `%APPDATA%`（Roaming）を避けた。同期やローミングで SQLite のファイルが書いている途中に運ばれると壊れることがある
- `C:\ProgramData` を避けた。端末で共有する場所だが、別の利用者が作ったファイルは既定では書き換えられず、
  SQLite の1ファイルを複数人で書くのにも向かない。複数人で使うなら、次のサーバーの形にする

## サーバーに置いて複数人で使う

DB は PostgreSQL にします。`NETVISION_DATABASE_URL` に接続先を渡すと、SQLite の代わりにそちらを使います。
表の作りは Alembic のマイグレーションで同じものを作るので、1台の形からサーバーの形へ、作り直しなしで移れます。

| 何 | 置き場所 |
|---|---|
| DB | PostgreSQL（`NETVISION_DATABASE_URL=postgresql+psycopg://...`） |
| 資料 | `NETVISION_DOCUMENT_STORAGE_PATH`（systemd の例では `/var/lib/netvision/documents`） |
| そのほか | `NETVISION_DATA_DIR`（systemd の例では `/var/lib/netvision`。`StateDirectory=netvision` で作る） |

systemd の例は `packaging/netvision.service`、Docker Compose は [INSTALL.md](INSTALL.md) にあります。
認証がまだ無いので、サーバーの形でも既定は `127.0.0.1` だけで待ち受けます。
