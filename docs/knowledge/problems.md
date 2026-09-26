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
