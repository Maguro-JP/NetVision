# 設計判断

なぜこの形にしたか、なぜ別案を採らなかったか。

## 更新 API の必須項目への null（2026-09-26 JST）

- 決めたこと: 更新用スキーマの `X | None` のうち、DB で空を許さない項目は `schemas/types.py` の
  `refuse_null` で 422 にする。省略は「変えない」のまま。
- 採らなかった案: 型を `X` にして省略時だけ許す（pydantic では省略と null を型で分けにくく、
  既存の「省略できる」を崩す）。
- 再発防止: `tests/test_api_robustness.py` が OpenAPI から全ての更新の経路と項目を拾って null を送る。

## CSV の閉じていない引用（2026-09-26 JST）

- 決めたこと: `strict=True` で読み、`unexpected end of data` のときだけ断る。それ以外の strict の
  失敗は、従来のゆるい読み方に戻す。
- 採らなかった案: 常に strict（`"本社"ビル` のような手書きの CSV が急に弾かれる）、
  「"」の数の偶奇で判定（`19" rack` のような引用していない欄の「"」で誤判定する）。

## 表題の行を飛ばして見出しを探す（2026-09-26 JST）

- 決めたこと: 先頭 10 行のうち、埋まっている欄が2つ以上の最初の行を見出しにする。
  結果の行番号は、飛ばした行も数えたファイルの行番号（`Table.first_row` が起点）。
- 採らなかった案: 知っている見出しの語で探す（台帳・拠点・結線・製品情報で語が違い、
  それぞれに語の一覧を持つことになる）。
- 制約: 見出しより下の空行は数えていない（従来どおり）。空行を挟んだ台帳では、
  それより後ろの行番号が空行の数だけ Excel とずれる。
- 変えたいとき: `services/tablefile.py` の `_header_index` と `_HEADER_SEARCH_ROWS`。

## 年が後ろに来る日付（2026-09-26 JST）

- 決めたこと: `4/1/2026` のように日と月がどちらも 12 以下で異なるときは断る。片方が 13 以上なら
  それを日として読む。
- 理由: 英語設定の Excel は CSV に月/日/年で書き出し、欧州の資料は日/月/年で書く。
  以前の日/月/年への決めつけでは、4月1日が1月4日になり、保守期限が黙ってずれた。
- 採らなかった案: 月/日/年に決めつける（欧州の資料で同じことが逆向きに起きる）。
- 変えたいとき: `services/importer.py` の `_parse_day_and_month`。

## 取り込み結果の updated の意味（2026-09-26 JST）

- 決めたこと: 機器の取り込みで、既存の機器のうち値が変わらず保守契約も増えない行は
  `unchanged` として数え、`updated` には含めない。行の action も `unchanged`。
- 理由: 書き出した台帳をそのまま戻すと「更新 120 件」と出て、何か書き換わったように見えた。
  拠点の取り込みは既に「変更なし」を分けていた。
- 影響: `updated` を「既存の機器の行数」として使っていた呼び出しは、`updated + unchanged` を見る。
  画面（取り込み画面の枠、裏の取り込みの完了の文）は合わせてある。

## Excel の書き出しに lxml を足さない（2026-09-26 JST）

- 測ったこと: 2 万台の台帳の Excel 書き出しは 6.1 秒（設定の読み直しを直した後）。openpyxl は
  lxml があると書き出しに使うが、入れても 5.4 秒（13% 減）だった。
- 決めたこと: 依存を増やすほどの効果が無いので入れない。時間の大半は openpyxl の XML の組み立て。
- 変えたくなったら: 16 万台で書き出しが待てない長さになったとき。CSV（2 万台で約 1.9 秒）を案内するか、
  裏の処理（jobs）に回して待たせない形を先に考える。

## Node.js の版は、その時点で最新の LTS に合わせる（2026-09-28 JST）

- 決めたこと: CI（`ci.yml`・`bundle.yml`）と `frontend/Dockerfile` の Node.js は、最新の LTS に合わせる。
  いまの 22 からは、26 が LTS になる 2026-10-28 以降に 26 へ上げる。
- 理由: 最新の LTS はサポートが最も長く、上げる回数が減る。いまの最新 LTS は 24 だが、10/20 に
  Maintenance に入り、8日後に 26 が LTS になるので、24 を挟むと1か月で2回上げることになる。
- 採らなかった案: いま 24 へ上げる（すぐ古い扱いになり、2028 年より前にもう一度上げる）。
- 上げ方: 手元で 26 を使って `npm ci`・型チェック・試験・ビルドを通してから、3か所の版を変える。
- 触る場所: `.github/workflows/ci.yml`・`.github/workflows/bundle.yml` の `node-version`、`frontend/Dockerfile` の `FROM`。

## CSV の書き出しでは、式の形の値の先頭に `'` を付ける（2026-09-28 JST）

- 決めたこと: CSV を書き出すとき、Excel が式として読む形の値（`=`・`+`・`@`・タブ・復帰で始まる値と、
  数字でない `-` で始まる値）だけ、先頭に `'` を付ける。取り込みでは、その形の `'` を1つ外す。
- 理由: NetVision は式を書き出すことが無い。`=` で始まる値が Excel で動くのは事故（CSV インジェクション）なので、
  文字として出す。CSV には文字の印が無く、値に手を入れる以外に防ぎようが無い。
- 見た目への配慮: `'` は Excel では見えないが、ほかの道具では見える。付ける値を式の形のものに絞り、
  `-5` のような負の数と `-` だけの欄には付けない。書き出して取り込み直すと元の値に戻る。
- 採らなかった案: CSV はそのまま出し、画面で xlsx を案内する（事故は防げない）。何もしない。
- 再発防止: CSV はすべて `csvsafe.writer` で書く。`csv.writer` を直接使うと `tests/test_export_csv_formula.py` が落ちる。
- 触る場所: `backend/app/services/csvsafe.py`（付ける条件）、`backend/app/services/tablefile.py`（取り込みで外す）。

## 指摘の出典は資料の名前で持ち、既定値に拠ったときだけ付ける（2026-10-03 JST）

- 決めたこと: `Finding.sources`（文字列の並び）を足し、資料の名前は defaults.py に既定値と並べて置く。既定値に拠った
  指摘にだけ付け、資料に届いていない機種は「未確認: …」と出す。まず L3 と結線の点検の API と画面に出した。
- 理由: 既定値は Config に書いていないので、根拠の行だけでは読み手が確かめられない。名前は版が変わっても探せる。
- 採らなかった案: URL で持つ（版ごとに変わり、届かない環境がある）。すべての指摘に付ける（拠っていない資料を出すと
  かえって紛らわしい）。
- 触る場所: `backend/app/services/defaults.py`（`*_SOURCE`、`static_distance_source`）、
  `backend/app/services/reviewnote.py`、`frontend/src/components/L3Panel.tsx`（`sourcesLine`）。

## Junos の論理システム（logical-systems）の中の静的経路は、口と一緒に読めるまで読まない（2026-10-04 JST）

- 決めたこと: `set logical-systems LS routing-options static …`（と、その中の routing-instances の static）は、いまは読まない。
  論理システムを読むときは、口（`set logical-systems LS interfaces …`）と経路を一緒に読み、別の機器として扱う。
- 理由: Juniper「Understanding Logical Systems for Routers and Switches」に「Logical systems perform a subset of the actions of
  the main router and have their own unique routing tables, interfaces, policies, and routing instances.」とある。経路だけを
  全体の機器に足すと、policyreview の「次の相手へ行けません」が、論理システムの口の帯を知らないまま判断して誤った HIGH を出す。
  routing-instances（#156）は口を全体の `set interfaces` に持つので、この問題は起きない。
- 採らなかった案: 論理システムの経路を vrf=LS として全体の機器に足す（口が無いので届く先の点検が誤る）。経路だけ足して届く先の
  点検から外す（点検ごとに例外が増え、論理システムの口・OSPF・BGP も同じ扱いを要する）。
- 触る場所: `backend/app/services/configparse/routing.py` の `_junos_static`、`backend/app/services/configparse/juniper.py`
  （論理システムを1台として切り出すなら、ここで Config を論理システムごとに分ける）。

## Junos の論理システムは、読めるまで「読んでいない」と結果に出す。読むときは1ファイルを複数の機器に分ける（2026-10-04 JST）

- 決めたこと: 論理システムの行（`set logical-systems …` か囲みの形の `logical-systems {`）がある Junos の設定は、
  設定の点検の結果の notes に「論理システムの中は読んでいない」と出す。黙って抜けると、その中の口・経路・誤りが
  「無い」ことになるため。
- 読むときの設計（分ける部品だけ作った。2026-10-10 JST、`juniper.split_logical_systems`。取り込みにはまだつないでいない）: `configparse/juniper.py` で平らにした set 行を、`logical-systems NAME` で始まる行と
  それ以外に分け、`logical-systems NAME ` を外した行を同じ読み手に通して、論理システムごとに別の ParsedConfig を作る。
  ホスト名は「全体のホスト名/NAME」とする。いまの取り込みは1ファイル→1台なので、parse が複数の ParsedConfig を
  返せる入口（例: `parse_all`）を足し、取り込み（pipeline・configcheck）は1台目を従来どおり扱い、残りを別の機器として
  足す形にする。口の名前は全体と論理システムで重なりうる（同じ物理口の別 unit）ため、機器を分けて持つ。
- 理由: 論理システムは自分の口・経路表・ポリシーを持つ（Juniper「Understanding Logical Systems for Routers and
  Switches」）。全体の機器に混ぜると、口の帯を知らないまま経路を判断して誤った指摘になる（前の項）。
- 採らなかった案: 何も出さずに読まない（黙って抜ける）。論理システムの行を警告（warning）にする（設定の誤りではない）。
- 触る場所: `backend/app/services/configcheck.py` の `_JUNOS_LOGICAL_SYSTEM`、読むときは `configparse/juniper.py` と
  `configparse/__init__.py` の `parse`。

## Junos の論理システムを取り込むときの設計（2026-10-12 JST。未実装）

- 決めたこと: 論理システムは「1つのファイルに何台分も入っている」の一種として扱う。
  `configs.ingest_many` が今 `configsplit.split` で台数ぶんに分けているのと同じ段で、Junos の塊に論理システムがあれば
  `juniper.split_logical_systems` で論理システムごとの塊を足し、1台ずつ `ingest` に通す。
  - 全体の塊は、元の字のまま渡す（世代に残す中身を変えない。今の読み手は論理システムの行を読まないので、全体の機器の値は変わらない）
  - 論理システムの塊は `set system host-name 全体のホスト名/論理システム名` を頭に足した set 行にする。台帳の機器名と、
    世代（ConfigSnapshot）のホスト名はこの名前になる
  - 論理システムの世代の中身は、元の字ではなく分けた set 行になる。結線の作り直し（`rebuild_links`）は世代の中身を
    読み直すので、分けた形で持てば読み直しても同じ結果になる
- 理由: `ingest_many` は既に「1ファイル→複数の機器」を扱っており、世代・台帳・結線・差分の作りを変えずに済む。
  AP の自動登録（`_register_aps`）と違い、論理システムは自分の口と経路を持つので、同じ読み手で読める形にして渡すのが素直
- 変わらないもの: 設定の点検（`configcheck.check`）は1ファイル1機器のまま。notes の「論理システムの中は読んでいない」も残す
  （点検を論理システムごとに出すのは別の段にする）
- 決めていないこと（実装の前に確かめる）:
  - 機器の種別（device_type）と拠点は全体の機器から写すか。AP の登録はコントローラの拠点を写している
  - `device_id` を指定して取り込んだとき（今は分けない）に、論理システムをどう扱うか。指定と食い違うので、分けない方に倒す案
  - 名前の区切りに `/` を使ってよいか（台帳・URL・画面で問題が出ないか）
- 採らなかった案: parse が複数の ParsedConfig を返す入口（`parse_all`）を足す（10/4 の案）。呼び出し元が8か所あり、
  全部を複数台に対応させる必要がある。`ingest_many` の分ける段に寄せれば、触るのは1か所で済む
- 触る場所: `backend/app/services/configs.py` の `ingest_many`、`backend/app/services/configparse/juniper.py` の
  `split_logical_systems`
