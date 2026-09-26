# 起きた問題と対策

## IOS の `switchport access vlan dynamic` で取り込みが落ちていた（2026-09-26 JST）

- 症状: VMPS の口を含む Config を読むと ValueError で全体が読めない。
- 原因: `configparse/cisco.py` が access vlan と native vlan の末尾を無条件に `int()` していた。
- 対策: 数字のときだけ番号を持つ。dynamic は番号空の access の口として読む。
  試験は `tests/test_configparse.py` の2件。
- 見つけ方: 見本の Config（`tests/fixtures/`、`docs/topology-samples/`）の行を
  削る・切る・語を足すなどして読み手に通し、例外を集めた（各300回）。
  このとき例外が出たのは Cisco の上記2か所だけだった。
  崩れた入力で落ちる箇所を探すのに安く効くので、読み手を足したら同じ方法で見るとよい。

## EXOS で切れたマスクの行により取り込みが落ちていた（2026-09-26 JST）

- 症状: `configure vlan X ipaddress 10.0.0.5 255.255.255.` のような切れた行で ValueError。
- 原因: `configparse/exos.py` のマスクの正規表現が `[\d.]+` で、何でも拾っていた。
- 対策: 4つ組か2桁までの長さだけを拾う。切れた行は読まない。
- 同じ方法を、試験に埋め込まれた 336 本の Config（25 ベンダー）にも各150回かけた。
  例外が出たのはこの1か所だけで、直したあとは 0 件。

## API の総当たりで見つけた 500（2026-09-26 JST）

OpenAPI の定義から全経路（194）に崩れた要求を送り、500 を集めた（各25回）。
加えて、PATCH / PUT の各項目に1つずつ null を送った。見つかったもの:

| 経路 | 原因 | 対策 |
|---|---|---|
| 機器・拠点・シナリオの PATCH（9項目） | 任意にした項目の null を NOT NULL の列へ書いた | `schemas/types.py` の `refuse_null` で 422 |
| `/topology/routes` | 機器の無い図で線の形を渡さずに返していた | 空の形を渡す |
| Excel と構成図の書き出し | 制御文字（ESC など）を openpyxl と XML が受け付けない | `services/xlsxsafe.py` と SVG の逃がしで落とす |
| `/blueprint/apply` の二度置き | 機器名の一意制約 | 先に見て 409 |

- 下地の都合で出るだけのもの（`/schema/apply` が Postgres に繋ぎに行く、裏の処理が
  別の DB を開く）は、試験の作りの問題で不具合ではない。
- 総当たりは手元の `backend/data/settings.json` を書き換える。試験の前提を崩すので、
  `NETVISION_DOCUMENT_STORAGE_PATH` を一時フォルダに向けて走らせる。
  試験（conftest）は今は自分で一時フォルダに向けている。

## 一意な項目の重複で 500 になっていた（2026-09-26 JST）

- 機器の一意な列は hostname・code（管理ID）・serial_number の3つ（`models/device.py`）。
  ホスト名は取り込みも API も重複を見ていたが、管理ID は取り込みで、シリアル番号は
  取り込みと API の両方で見ておらず、DB の一意制約の 500 になっていた。
- 取り込みは `importer._UNIQUE_FIELDS` を回して行のエラーにする。API は 409。
- 一意な列を足したら、この2か所（取り込みの `_UNIQUE_FIELDS`、`routes/devices.py` の
  `_ensure_unique_*`）にも足すこと。見つけ方は、台帳の取り込みに崩れた CSV / Excel を
  送る総当たり（種を変えて3回、各120ファイル × 5経路）。
- 空文字のシリアル番号は重複の元になるので、スキーマで空なら未設定にしている。

## CSV の閉じていない「"」で、後ろの行が黙って消えていた（2026-09-26 JST）

- 症状: 3000 行の台帳の6行目に閉じていない「"」があると、「5行・エラー0件」として通る。
  128KB を超えると csv.Error の 500。
- 原因: `csv.reader` のゆるい読み方は、引用の途中でファイルが終わっても何も言わない。
- 対策: `tablefile._csv_records` で `strict=True` で読み、`unexpected end of data` のときだけ断る。
  それ以外の strict の失敗（`"x"y` など）は、手書きの CSV を急に弾かないよう従来の読み方に戻す。
- 見つけ方: ファイルを受け付ける 10 経路に、壊れたファイル（空、ゴミ、偽の PDF、中身の違う ZIP、
  壊れた XML、NUL、UTF-16、2MB の1行）を5つの拡張子で送った。500 はこの件だけだった。

## 黙って違う値になる取り込み（2026-09-26 JST）

| 入力 | 以前 | 今 |
|---|---|---|
| 日付 `4/1/2026`（英語設定の Excel の月/日/年） | 1月4日として保存 | 年/月/日で書くよう行のエラー（片方が 13 以上なら読む） |
| 保守年額 `nan`（pandas の CSV） | 数として通り、合計が壊れる | 行のエラー |
| 保守年額 `-50000` | 保存され、その機器の契約一覧が 500 | 行のエラー |
| JSON の `NaN`・`Infinity`・`1e400` | 保存される、または検証エラーの応答で 500 | 入口で 422（`app/strictjson.py`） |

- 見つけ方: 台帳でありそうな日付・金額の書き方を並べて読み手に通し、結果を目で見た。
  例外が出ないものほど見落としやすいので、「読めた値が正しいか」まで見る。
- まだの候補: 金額の「48万円」「480千円」は読めずに行のエラーになる（黙っては変わらない）。

## 2 万台で構成図の書き出しが止まる・遅い（2026-09-26 JST）

- SVG の全体図の書き出しが、画面と違って台数で絞っておらず、900 秒たっても終わらずに
  サーバーごと止まった（メモリ不足の強制終了と見ている。ログは残らず、確かめてはいない）。
  `topology.auto_scoped_ids` を画面・SVG・ワークブックで共有した。
- 結線表が、使わない図の配置（機器の数の2乗）まで計算していた。`get_graph(arrange=False)`。
- 測り方の注意: GET を続けて送ると、重い要求が終わらないうちに次が詰まり、軽い経路まで
  打ち切りに見える。1 つずつ、前の要求が終わってから測る。

## ときどき出る SQLAlchemy の警告（2026-09-26 JST、未解明）

- `tests/test_background_import.py::test_the_dashboard_lists_what_ran` で、全体を流したときに
  1回だけ `SAWarning: Identity map already had an identity for (Device, (1,))`（`importer.py` の flush）。
  単独で5回・全体で2回流して再現しなかった。裏の処理と試験の DB の共有のタイミングと見ているが、確かめていない。

## 配布版で配信元の外のファイルが読める（2026-09-27 JST）

- 症状: `NETVISION_SERVE_FRONTEND` で画面を配るとき、`GET /../netvision.db` のような
  パスで、配信元より上にあるファイル（データベースや設定）がそのまま返った。
- 原因: uvicorn は `%2F` や生の `..` を解いたパスを渡す。`main._mount_frontend` の SPA の経路が
  それを配信元に連結して、そのまま返していた。`/assets` 配下は StaticFiles が自分で防いでいた。
- 対策: 解決したパスが配信元の中にあるときだけ返し、外なら画面の入口（index.html）を返す。
- 試験: `tests/test_serve_frontend.py`。httpx は `..` を先に畳むので、ASGI を直接呼んで生のパスを渡す。
- 影響: 配布版（start.sh・start.bat・standalone.py）で、この機能を開ける人すべて。
  開発時の Vite 経由の起動は当たらない。
