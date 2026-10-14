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

## ときどき出る SQLAlchemy の警告（2026-09-26 JST、2026-09-27 に解明）

- 症状: `tests/test_background_import.py::test_the_dashboard_lists_what_ran` で、全体を流したときに
  まれに `SAWarning: Identity map already had an identity for (Device, (1,))`（`importer.py` の flush）。
- 原因: 試験の DB は1本の接続を全セッションで共有する（conftest の StaticPool）。この試験は
  取り込みを2本同時に裏で走らせるので、2つのスレッドの書き込みが同じ接続の上で重なっていた。
  スレッドの中の警告と失敗は試験を落とさないので、見えにくかった。
- 確かめ方: 同時に2本を50回（100件）。共有の接続では失敗2件・警告1回、接続を分けたファイルの
  SQLite では失敗0件・警告0回。本番（接続が分かれる）では起きないと見ている。
- 対策: 一覧の並びを見る試験なので、1本目を終えてから2本目を始める。
- 残り: PostgreSQL で同じ台帳を2本同時に取り込むと、両方が「無い」と見て同じ hostname を
  入れようとし、片方が UNIQUE 制約で失敗しうる。制約が守るので台帳は壊れない。
  手元に PostgreSQL が無く、確かめていない。

## 配布版で配信元の外のファイルが読める（2026-09-27 JST）

- 症状: `NETVISION_SERVE_FRONTEND` で画面を配るとき、`GET /../netvision.db` のような
  パスで、配信元より上にあるファイル（データベースや設定）がそのまま返った。
- 原因: uvicorn は `%2F` や生の `..` を解いたパスを渡す。`main._mount_frontend` の SPA の経路が
  それを配信元に連結して、そのまま返していた。`/assets` 配下は StaticFiles が自分で防いでいた。
- 対策: 解決したパスが配信元の中にあるときだけ返し、外なら画面の入口（index.html）を返す。
- 試験: `tests/test_serve_frontend.py`。httpx は `..` を先に畳むので、ASGI を直接呼んで生のパスを渡す。
- 影響: 配布版（start.sh・start.bat・standalone.py）で、この機能を開ける人すべて。
  開発時の Vite 経由の起動は当たらない。

## = で始まる値が Excel の式として書き出される（2026-09-27 JST）

- 症状: 機器名や型番が `=` で始まると、xlsx の書き出しで式になった。「=== 要確認 ===」は
  壊れた式、`=HYPERLINK(...)` は開いた人の Excel でリンクとして動いた。`#N/A` はエラー値になった。
- 原因: openpyxl は文字列の型を推定し、`=` で始まれば式、ERROR_CODES に一致すればエラー値にする。
- 対策: `xlsxsafe.cell_value` で、その形の文字列だけ `CellRichText` で包む。型の推定を通らずに
  文字列として書かれ、読み戻すと元の文字列になる。`cell_value`・`row_values` を通らない書き方を
  足すと、また式になる。
- 試験: `tests/test_export_formula_text.py`（xlsx の5経路に `<f>` と `t="e"` が無いこと、読み戻し）。
- 残り: CSV は値を変えないと防げないので、判断待ち（docs/reports/pending.md）。

## アップロードの上限の抜け（2026-09-27 JST）

- 見た経路: 台帳・拠点・結線の取り込み、syslog、文書、Config、Config 計画の突き合わせ、MIB、
  製品リファレンス。どれも上限と 413 を持っていた。
- 抜け: `/configs/preview`（取り込み前の確認）だけ上限が無く、取り込みで断られる大きさでも
  読み取りまで走った。取り込みと同じ `MAX_CONFIG_BYTES` を当てた。試験は `test_configs_api.py`。
- 残り: どの経路も全体を読んでから大きさを見る。上限の手前で打ち切る読み方にはしていない。

## 別のサイトのページから台帳を書き換えられる（2026-09-27 JST）

- 症状: 認証が無いので、利用者がブラウザで開いた別のサイトのページから、multipart の
  ファイル送信（台帳の取り込みなど）と本文の無い POST（バックアップの作成など）が通った。
  JSON の本文は CORS の事前確認で止まるが、この2つは事前確認なしに送られる。
- 対策: `app/originguard.py`。POST・PUT・PATCH・DELETE に別のサイトの Origin が付いていれば 403。
  通すのは、Origin 無し（curl・MCP）、`cors_origins`、手元（localhost・127.0.0.1・::1）、
  Host と同じ宛先。試験は `tests/test_origin_guard.py`。
- 残り: DNS リバインディング（攻撃者の名前を 127.0.0.1 に向け直す手口）では Origin と Host が
  一致するので防げない。防ぐなら Host を許した名前に限る仕組み（TrustedHost）が要る。
  LAN の IP で開く使い方と両立させる設定が要るので、まだ入れていない。

## 投入手順に show の出力が混ざる（2026-09-27 JST）

- 症状: 取り込んだ Config はログのまま（`show version` → `show running-config` → `show cdp neighbors`）
  保存される。差分がそれをそのまま設定として比べていたため、次のことが起きた。
  - 版数や隣接機器の表示が変わると、投入手順に `no Cisco IOS XE Software, Version ...` が入る
  - 投入後の突き合わせが、稼働時間や版数が変わっただけで「一致しない」になる
  - 計画と是正案の「現行フル」（故障時の復旧に貼るもの）に show の出力が入る
- 対策: `configdiff/logtext.py` の `config_part`。プロンプト付きの設定を出すコマンド
  （`show running-config`、`show configuration`、`display current-configuration` など）の直後から
  次のプロンプト行までを設定とみなす。`Building configuration...` と `Current configuration : N bytes`
  も除く。プロンプト行の無い文字列はそのまま。`configdiff.build` の入口と、計画・是正案の現行フルで使う。
- 試験: `tests/test_configdiff_logtext.py`（直す前は 3 件落ちた）。是正案と脆弱性の試験は、見本の末尾
  （show cdp の出力の後ろ）に設定の行を足していたので、`end` の手前に足す形に直した。
- 続き（同日）: Config の下書き（config-draft）の依頼文も、現行をログのまま先頭から決まった長さだけ
  載せていたため、show version で枠を使い、隣接機器の表示まで AI に渡していた。`config_part` を通す。
  STP・HA・route-target の読み取りは行頭が設定の書き方のときだけ拾うので、そのままにした。
- 未確認: プロンプトとコマンドの形は手元の見本（IOS・Junos）と既存の `_SHOW_OUTPUT` に合わせた。
  各社のマニュアルでは確かめていない。形が合わないログは、今までどおりそのまま比べる。

## RFC 5424 の syslog が時刻も機器名も読めない（2026-09-28 JST）

- 症状: `<34>1 2026-09-27T10:00:00.000Z sw1 app - - - msg` の形の行で、時刻と機器名が空になり、
  機器ごとの集計に入らなかった。PRI の後の版数（`1`）と、小数秒・時差付きの時刻を想定していなかった。
  `<999>` のように範囲外の PRI も、重さ（999 % 8）として読んでいた。
- 対策: `syslog._RFC5424`。形は RFC 5424 "The Syslog Protocol" 6 章の ABNF に合わせた（原文を確認）。
  時差付きの時刻は、時差の無いほかの形と並ぶよう表示の時間帯に直す。PRIVAL は 0〜191（6.2.1）。
- 試験: `tests/test_syslog.py` の末尾 3 件（直す前は 3 件とも落ちた）。
- 崩れた入力（空・バイナリ・NUL・2MB の1行・Shift_JIS・2月30日）は、直す前から 500 にならなかった。

## SNMP の Hex-STRING が値として読めない（2026-09-28 JST）

- 症状: `SNMPv2-MIB::sysName.0 = Hex-STRING: 73 77 31` で、機器名が「Hex-STRING: 73 77 31」になった。
  型名の読み取りが `-` を許していなかった。日本語の sysLocation も net-snmp は Hex-STRING で出す。
  値の空の sysName は、機器名が空文字になった。
- 対策: 型名は net-snmp の `snmplib/mib.c` が付けるもの（`Hex-STRING: `、`Network Address: `、
  `Wrong Type (should be ...): ` など、ソースで確認）に合わせる。Hex-STRING は UTF-8 の文字として
  読めるときだけ文字に戻し、MAC アドレスなどは16進のまま。空の sysName は機器名なし。
- 試験: `tests/test_mib.py` の末尾 3 件（直す前は 3 件とも落ちた）。
- 文書検索（`'` `%` `NEAR(` `*` など）と、崩れた MIB（空・バイナリ・3MB の1行）は 500 にならなかった。

## SNMP の OID の突き合わせ（2026-09-28 JST）

- 確かめたこと: `mib.NUMERIC` の OID を公式の定義と突き合わせた。ENTITY-MIB（RFC 6933）の
  entPhysicalSoftwareRev=10・SerialNum=11・ModelName=13、IF-MIB（RFC 2863）の ifDescr=ifEntry 2・
  ifSpeed=5・ifAdminStatus=7・ifOperStatus=8・ifHighSpeed=ifXEntry 15・ifAlias=18・ifNumber=interfaces 1。
  いずれも一致した。HOST-RESOURCES-MIB の hrProcessorLoad（1.3.6.1.2.1.25.3.3.1.2、直近1分の平均）は
  net-snmp 同梱の MIB、CISCO-PROCESS-MIB の cpmCPUTotal5minRev（1.3.6.1.4.1.9.9.109.1.1.1.1.8、
  直近5分）は cisco/cisco-mibs の定義で確かめ、一致した（2026-09-28）。
- 直したこと: ifOperStatus の値は up(1)〜lowerLayerDown(7) の 7 つある（RFC 2863）。down(2) だけを
  「使う設定なのに落ちている」としていたので、notPresent(6)・lowerLayerDown(7) を加えた。4〜7 の表示も
  「状態が読めません」から、それぞれの意味に直した。試験は `tests/test_mib.py`。
- 気づいたこと: RFC 2863 の ifDescr は「メーカー・製品名・版を含む説明」と定義されていて、ポートの名前は
  ifName（ifXEntry 1）。多くの機器は ifDescr にも名前を入れるので、今は変えていない。

## AOS-S の読み手がスタックの口の範囲と口の側の VLAN を読まなかった（2026-09-28 JST）

- 確かめた資料: HPE Aruba「VLAN and Link-Aggregation Interoperability ArubaOS-Switch and Cisco IOS」の作例と、
  Airheads コミュニティの作例。表番号は未確認。
- 直したこと: `untagged 1/1-1/4`・`A1-A3`・`trunk 1/49-1/50 trk1 lacp` のように前置きの付いた範囲を
  1つの口として持っていたので、前置きが両端で同じときだけ開くようにした。前置きの違う `1/49-2/1` は開かない。
  `interface Trk1` の下の `tagged vlan 20-22,25`・`untagged vlan 30` も読むようにした。試験は
  `tests/test_configparse_arubaos.py`。

## AlliedWare Plus の範囲書きの口と、名前の無い VLAN が抜けていた（2026-09-28 JST）

- 確かめた資料: Allied Telesis「VLANs Feature Overview and Configuration Guide」（C613-22001-00 REV N）p.13〜14、
  「Getting Started with AlliedWare Plus」の既定の設定の作例、同社サポートポータルの作例。
- 直したこと: `interface port1.0.1-1.0.4` を1つの口として持ち、間の口が抜けていた。口1つずつに開くようにした。
  `vlan 20-22 state enable` のように名前を付けずに作った VLAN も読むようにした。試験は `tests/test_configparse_allied.py`。
- 未確認: `<vid-range>` にカンマ区切りが使えるか、経路と `access-list hardware` の書き方（コマンドリファレンスに当たれていない）。

## FortiGate の口の中の入れ子で、後ろの口が全部抜けていた（2026-09-28 JST）

- 確かめた資料: Fortinet「FortiOS CLI Reference」の `config system interface`（7.0.1・7.6.6）と、
  Administration Guide「IPv6 quick start」の作例。
- 起きていたこと: 口の中の `config ipv6 … end` の `end` を、`config system interface` の終わりと取り違えて
  読み終えていた。その口より後ろの口が全部台帳から抜け、同じ口の `set role` も読まれていなかった。
  `config secondaryip` の `set ip` で1つ目の住所を上書きし、その中の `edit 1` を口として作ってもいた。
- 直したこと: 口の読み手で入れ子の深さを数え、口そのものの高さの行だけを読む。ほかの節の読み手
  （`fortitext.blocks`）は元から数えていて、口の読み手だけが自前の数え方だった。試験は
  `tests/test_configparse_fortinet.py`。
- 気づいたこと: `vlan10` という口の名前が、別の処理で `Vlan10` に揃えられる。今回は触っていない。

## AireOS の口の表で、管理の口などがほとんど読めていなかった（2026-09-28 JST）

- 確かめた資料: Cisco「Wireless LAN Controller Port and Interface Command Reference, Release 7.4」、
  「Cisco Mobility Express Command Reference, Release 8.10」、技術資料「Configure VLANs on Wireless LAN Controllers」の
  `show interface summary` の作例。
- 起きていたこと: VLAN の欄を数字だけ、ポートの欄を数字・`LAG`・`--` だけとして読んでいた。公式の作例にある
  `management 1 untagged`、`redundancy-port - untagged`、`service-port N/A N/A`、`virtual N/A N/A`、名前に空白の入る
  `vlan 81` がすべて落ち、管理の口そのものが台帳に出なかった。
- 直したこと: 公式の作例に出る値をすべて読む。`untagged`・`N/A` は VLAN 番号を持たない口として読む。
  試験は `tests/test_configparse_aireos.py`。
- 未確認: `show wlan summary`・`show ap summary`・`show sysinfo` の列（公式の作例に当たれていない）。

## Arista の route-target を読んでいなかった（2026-09-28 JST）

- 確かめた資料: Arista「EOS User Manual」の「Inter-VRF Local Route Leaking」、Cisco「MPLS: Layer 3 VPNs Configuration
  Guide, Cisco IOS XE 17」、Juniper「Junos OS CLI Reference」の vrf-target。
- 起きていたこと: 読み手の説明には「Cisco / Arista」とあったが、Arista は route-target を `router bgp` の下の
  `vrf X` の下に `route-target export vpn-ipv4 10:10` の形で書くので、1つも読めていなかった。仮に読めても
  `vpn-ipv4` を値として拾う形だった。Junos の `vrf-target auto` も `auto` を値として持っていた。
- 直したこと: `router bgp` の下の `vrf` を読み、アドレスファミリーの語を飛ばす。値は `A:B` の形のものだけを持つ。
  試験は `tests/test_routetarget.py`。
- 未確認: NEC QX の `vpn-target`（公式の資料に当たれていない）。

## Arista MLAG で、heartbeat の宛先を相手のアドレスにしていた（2026-09-28 JST）

- 確かめた資料: Arista「Security Advisory 0161」の `mlag configuration` の作例と `show mlag` の出力。
- 起きていたこと: `peer-address heartbeat 172.30.118.190 vrf MGMT` の行を `peer-address` と同じに読み、相手のアドレスを
  `heartbeat` という語にしていた。heartbeat は二重主の検出に使う別の宛先で、`show mlag` でも `hb-peer-address` として
  別に出る。
- 直したこと: `peer-address heartbeat` の行は相手のアドレスとして読まない。試験は `tests/test_hareview.py`。
- 未確認: この読み手の FortiGate HA・ASA failover・Aruba VSX・Cisco vPC・IRF・iStack / CSS・StackWise の書き方。

## ASA failover で、相手のアドレスを読んでいなかった（2026-09-28 JST）

- 確かめた資料: Cisco「CLI Book 1: Cisco ASA Series General Operations」の failover の作例と、技術資料
  「Configure ASA Active/Active Failover in Firepower 4100 Series」。
- 起きていたこと: 役割（primary / secondary）と failover 用の口は読んでいたが、相手のアドレスは空のままだった。
- 直したこと: `failover interface ip <名前> <住所> <マスク> standby <住所>` の前の住所は primary に、standby の住所は
  secondary に付く（同ガイドの説明）ので、自分の役割の反対側を相手のアドレスとして持つ。Stateful 用の口
  （`failover link`）の行と取り違えないよう、`failover lan interface` の名前に合う行だけを見る。試験は
  `tests/test_hareview.py`。

## FortiGate HA で、入れ子より後ろの優先度とハートビート線を落としていた（2026-09-28 JST）

- 確かめた資料: Fortinet「FortiOS CLI Reference」の `config system ha`（7.0.1・7.2.7・7.4.2）。
- 起きていたこと: `config system ha` を字下げで読み、中の入れ子（`config ha-mgmt-interfaces` など）の `end` で読み終えていた。
  入れ子より後ろの `set hbdev`・`set priority` が抜け、2台の優先度が同じかどうかの確かめが働かなかった。
- 直したこと: 入れ子の深さを数え、`config system ha` の直下の行だけを読む。`config vcluster` の中の `set priority`
  （仮想クラスターの優先度）を機器の優先度と取り違えない。試験は `tests/test_hareview.py`。
- 同じ形の見落とし: FortiGate の口の読み手でも、入れ子の `end` で読み終える不具合があった（#82）。FortiOS の
  入れ子は `fortitext.blocks` か深さを数える形で読む。

## 単体の Catalyst を StackWise のスタックと読んでいた（2026-09-28 JST）

- 確かめた資料: Cisco「Command Reference, Cisco IOS XE Fuji 16.8.x (Catalyst 9300)」の Stack Manager and High Availability
  Commands、「Cisco IOS XE 17 - Stacking」、技術資料「Create and Manage Catalyst 3750 Switch Stacks」。
- 起きていたこと: `switch N priority M` の行があれば、`provision` が1台分でも（無くても）スタックとしていた。単体の
  3750 にも `switch 1 provision` は自動で作られ、`switch 1 priority` も設定の行として書けるので、冗長でない機器が
  冗長構成に見えていた。
- 直したこと: `provision` された機器が2台以上あるときだけスタックとする。優先度は、いちばん大きい値を持つ
  （大きいほうが active に選ばれやすい）。Catalyst 9300 では `switch priority` は EXEC のコマンドで running-config には
  出ない。試験は `tests/test_hareview.py`。

## 単体の Comware 機器を IRF の組と読んでいた（2026-09-28 JST）

- 確かめた資料: H3C「Automatic IRF Setup Configuration Examples」「Example for Configuring IRF Setup with Indirectly Connected
  Members」「S12500 Ethernet Link Aggregation Configuration Examples」の Configuration files。
- 起きていたこと: `irf member N priority M` か `irf domain` が1行でもあれば IRF の組としていた。組む前の単体の機器にも
  `irf member 1 priority 1` が書かれるので、冗長でない機器が冗長構成に見えていた。既存の試験（`irf domain 10` と1台分の
  priority）も、この誤った前提で書かれていた。
- 直したこと: `irf member N priority M` が2台分以上あるときだけ組とする。優先度はいちばん大きい値を持つ。
  試験は `tests/test_hareview.py`（既存の試験は公式の形の2台分に直した）。
- 同じ形の見落とし: StackWise（#90）と同じ。「1台の中で組む仕組み」は、メンバーが2台以上書かれているかで判定する。

## 組む前の VRP スイッチを iStack のスタックと読んでいた（2026-09-28 JST）

- 確かめた資料: Huawei「CloudEngine 8800, 6800, and 5800 V200R023C00 Configuration Guide - Virtualization」の「Example for
  Establishing a Stack of Two Switches」と、CloudEngine Troubleshooting Guide。
- 起きていたこと: `stack` の塊か `stack member N priority` が1行でもあればスタックとしていた。組む前のスイッチにも `stack`
  の塊があり1台分の行が書かれるので、冗長でない機器が冗長構成に見えていた。既存の試験も1台分の行で期待していた。
- 直したこと: `stack member N domain|priority` が2台分以上あるときだけスタックとする。組の番号は domain、優先度は最大。
  試験は `tests/test_hareview.py`（既存の試験は公式の形の2台分に直した）。
- これで HA ペアの読み手の8つの仕組みは、すべて公式の資料で確かめた。1台の中で組む仕組み（StackWise・IRF・iStack）は
  3つとも同じ見落としがあった。

## Huawei・H3C の IGMP スヌーピングと RP の範囲を読み違えていた（2026-09-28 JST）

- 確かめた資料: Huawei S300/S500/S2700/S5700/S6700 V200R024C00 Command Reference の igmp-snooping querier enable と
  static-rp (IPv4)、H3C「IGMP snooping configuration / commands」（Comware 7）、Cisco IOS XE 16.12.x Command Reference の
  ip pim rp-address。
- 起きていたこと:
  - Huawei の querier は `igmp-snooping querier enable` と書くのに、`igmp-snooping querier` で終わる行だけを見ていた。
    querier を入れた帯にも「querier が居ない」と出ていた。
  - H3C は `igmp-snooping` だけの行で全体に入れ、囲みの中の `enable vlan 2 to 10` で帯をまとめて入れる。どちらも拾えず、
    スヌーピングを使っていないことになっていた。
  - Huawei の `static-rp A preferred` は preferred を、`static-rp A acl-name 名前` は acl-name を範囲として持っていた。
    Cisco の `ip pim rp-address A 5 bidir` も `5 bidir` を範囲にしていた。
- 直したこと: 3つとも公式の書式どおりに読む。preferred・bidir・override は範囲から外す。`enable vlan` は IGMP-snooping の
  囲みの中だけで読む。試験は `tests/test_multicast.py`。
- 未確認のまま: NX-OS、Arista、Junos、NEC QX、Aruba CX のマルチキャストの書き方。

## F5 の IPv6 と A10 の use-if-ip を読み違えていた（2026-09-29 JST）

- 確かめた資料: F5 BIG-IP TMSH Manual の「ltm virtual」「ltm pool」、A10「Installing vThunder on GCP」と
  ARM テンプレートの「Verify Deployment」の show running-config。
- 起きていたこと:
  - F5 は IPv6 のアドレスとポートを `.` で区切る（`2001:db8::10.443`）。最初のコロンで切っていたので、受け口は
    アドレス `2001`、ポート `db8::10.443` になっていた。プールのメンバーも同じ。
  - A10 の `slb virtual-server vip use-if-ip ethernet 1`（口のアドレスを借りる受け口）は、行の形が合わず
    受け口として拾えていなかった。
- 直したこと: F5 は `:` が2つ以上ある（IPv6）ときは最後の `.` でポートを分ける。A10 は `use-if-ip` の受け口を
  アドレス無しで持つ。試験は `tests/test_loadbalance.py`。
- 未確認のまま: FortiGate の realservers の書き方。

## FortiGate の realservers を公式の形の一部しか読めていなかった（2026-09-29 JST）

- 確かめた資料: FortiOS CLI Reference（6.4.8 / 7.0.19 / 7.6.4）の config firewall vip。
- 起きていたこと:
  - `set type address` と `set address <アドレスオブジェクト名>` で書いた振り分け先は、宛先が空になっていた。
  - 振り分け先ごとの `set monitor` を見ていなかったので、監視を付けていても「監視が無い」扱いになっていた。
  - edit を数える位置を囲みで分けていなかった。vip の中の `config ssl-cipher-suites` の `edit 1` を vip の名前と
    取り違える作りだった。
- 直したこと: 囲みの並びを積んで、`firewall vip` 直下の edit だけを vip 名とする（VDOM の中でも同じ）。アドレス
  オブジェクト名と振り分け先ごとの監視を読む。試験は `tests/test_loadbalance.py`。
- 決めたこと: `set status standby` は止めた扱いにしない。控えとして用意したもので、手で止めたものではないため。

## NX-OS の vlan configuration の囲みを読めていなかった（2026-09-29 JST）

- 確かめた資料: Cisco Nexus 9000 Series NX-OS Multicast Routing Configuration Guide（7.x / 10.2(x) / 10.3(x)）
  「Configuring IGMP Snooping」。
- 起きていたこと:
  - NX-OS は帯ごとのスヌーピングを `vlan configuration 2` の囲みに書く。`vlan 2` の形しか見ていなかったので、
    帯ごとの設定が拾えていなかった。
  - `ip igmp snooping querier-timeout 300` を querier の行と読んでいた（`\b` は `-` の前でも切れる）。
  - 囲みの中の `no ip igmp snooping` を、機器全体でスヌーピングを止めた行と読んでいた。
- 直したこと: `vlan configuration 10,20-22` の囲みを帯の番号に開いて読む。querier は語の後ろが空白か行末の
  ときだけ。囲みの中の `no ip igmp snooping` はその帯だけを止める。試験は `tests/test_multicast.py`。
- 未確認のまま: Arista、Junos、NEC QX、Aruba CX のマルチキャストの書き方。

## Junos のマルチキャストを公式の形の一部しか読めていなかった（2026-09-29 JST）

- 確かめた資料: Juniper「Enterprise Layer 3 Multicast」Implementation Guide、Junos OS「IGMP Snooping Overview」
  「vlan (IGMP Snooping)」。
- 起きていたこと:
  - `set protocols pim interface fxp0.0 disable`（管理口を PIM から外す行）を、PIM の口として数えていた。
  - `rp local family inet address A`（この機器が RP）を「候補方式で他が決める」扱いにし、RP の住所を持っていなかった。
  - 1つの VLAN の `l2-querier` で、全部の VLAN に querier が居ることにしていた。
  - igmp-snooping の VLAN は名前で書くのに、名前の中の数字を VLAN 番号にしていた（`SALES` は拾えず、`floor2` は 2 になる）。
  - 旧 EX の `igmp-snooping vlan X disable` を「有効」と読んでいた。
- 直したこと: 5つとも公式の書き方どおりに読む。VLAN の番号は `set vlans <名前> vlan-id <番号>` で引き、無いときだけ
  名前の数字を使う（これまでの動きを残すため）。試験は `tests/test_multicast.py`。
- 未確認のまま: Arista、NEC QX、Aruba CX のマルチキャストの書き方。

## AOS-CX の候補 RP と帯の口の IGMP を読めていなかった（2026-09-29 JST）

- 確かめた資料: AOS-CX Multicast Guide（10.09 / 10.10 / 10.15）の「IGMP configuration example」「PIM-SM configuration
  example」「Configuring an RP」、CLI Bank の router pim。
- 起きていたこと:
  - AOS-CX は候補方式を `router pim` の中に `rp-candidate` / `bsr-candidate` と書く。`ip pim` で始まる行しか見て
    いなかったので、候補方式で組んだ機器に「RP が決まっていません」と出ていた。
  - AOS-CX は帯の口（`interface vlan2`）で `ip igmp enable` にすると、その帯の querier になれる（IGMPv3 では既定で
    有効）。これを見ていなかったので、querier が居る帯にも「querier が居ない」と出ていた。
  - 帯の中の `ip igmp snooping disable` を読んでいなかった。
- 直したこと: 3つとも公式の書き方どおりに読む。帯の口の IGMP は、スヌーピングを入れた帯にだけ querier として
  付ける（帯の口があるだけでスヌーピングの帯を作らない）。試験は `tests/test_multicast.py`。
- 未確認のまま: Arista、NEC QX のマルチキャストの書き方。

## Arista の RP を読めておらず「RP が決まっていません」と出ていた（2026-09-29 JST）

- 確かめた資料: Arista EOS 4.36.2F User Manual「Protocol Independent Multicast」。
- 起きていたこと:
  - Arista の静的 RP は `router pim sparse-mode` の `ipv4` の中に `rp address A [グループ] [override]` と書く。読み手は
    `ip rp address` と `rp-address` しか見ていなかったので、公式の形で書いた RP が読めず「RP が決まっていません」と出ていた。
  - 候補方式の `rp candidate vlan 24 ...`（`router pim sparse-mode` の中）と `candidate ...`（`router pim bsr` の中）も
    拾えていなかった。
- 直したこと: どちらも `router pim` の囲みの中で読む。試験は `tests/test_multicast.py`。
- 未確認のまま: Arista の IGMP スヌーピングの書き方、既存の試験にある `ip rp address` の形、NEC QX。

## 1行目に文言が続く banner の中身を、設定として読んでいた（2026-09-29 JST）

- 確かめた資料: Cisco IOS Command Reference の banner motd と、Configuration Fundamentals Configuration Guide 15M&T
  「Managing Connections, Menus, and System Banners」。
- 起きていたこと: 閉じの印は「区切りに選んだ1文字が2回目に出たところ」なのに、`banner motd ^CWelcome` の `^CWelcome`
  を丸ごと印として探していた。閉じが見つからないので何も外さず、文言の中の `interface GigabitEthernet0/99` が
  無い口として台帳に載っていた。
- 直したこと: 印は1文字（保存した設定の `^C` は2文字で1つ）とし、印の後ろに同じ行で続く文言も扱う。同じ行で閉じる
  banner はその行だけを外す。英数字で始まるもの（ASA の `banner motd 文言`）は印とみなさない。試験は
  `tests/test_configparse_banners.py`。
- 未確認のまま: 保存した設定で印が `^C` と出る形（既存の試験の形に合わせた）。NEC QX のマルチキャストは、マニュアル置き場
  （support.nec.co.jp）にこの環境から届かず確かめられなかった。

## VLAN の範囲で 0 を通していた（2026-09-29 JST）

- 確かめた資料: Huawei CX320 Command Reference の port trunk allow-pass vlan、S7700 V200R024C00 Configuration Guide
  「Assigning VLANs」、Cisco VLAN Configuration Guide「Configuring VLAN Trunks」。
- 起きていたこと: 1つだけ書いた番号は 1〜4094 に絞っていたが、範囲（`0 to 10`）は上端しか見ておらず、機器が受け付けない
  VLAN 0 を許可 VLAN に入れていた。
- 直したこと: 範囲も両端を 1〜4094 に絞る。試験は `tests/test_vlan_trunk.py`。
- 未確認のまま: NEC QX の `port trunk permit vlan`、Junos の `vlan members`、Huawei の `port trunk allow-pass only-vlan` が
  保存した設定でどう出るか（今は読んでいない）。

## VRP の PHY 列の印で、表の読み取りが止まっていた（2026-09-29 JST）

- 確かめた資料: Huawei「Troubleshooting S Series Switch Port and Interface Problems」の display interface brief の凡例、
  S Series Troubleshooting Guide「Regular Expression in display Commands」の出力例。
- 起きていたこと: PHY 列には `*down` のほか `^down`（控え）、`~down`（LDT）、`#down`（LBDT）と、`up(s)` `down(e)` の
  ような括弧の印が付く。読み手は `up` `down` `*down` しか知らず、知らない値の行で表を読むのをやめていた。
  1行目が `^down` なら、その機器のどの口も線の上下が分からないままになっていた。
- 直したこと: 括弧の印は外して上下を読み、頭の印は `*` を人が止めた、`^` を down、`~` と `#` を err-disabled に
  そろえる（下流の linkcheck と topology は err-disabled を「上がっていない」として扱う）。試験は `tests/test_linkstate.py`。
- 未確認のまま: linkstate.py のほかの表（Cisco・NX-OS・Junos・Aruba CX・PAN-OS・FortiGate・NEC QX・FastIron・アラクサラ）。

## show interfaces status の知らない言葉で、表の読み取りが止まっていた（2026-09-29 JST）

- 確かめた資料: Cisco IOS XE Interface and Hardware Components Configuration Guide（Catalyst 9300 / 9400、16.11.x・17.2.x）
  「Checking Port Status and Connectivity」の show interface status の出力例。
- 起きていたこと: Status 列の言葉が手元の一覧に無い行（光モジュールが無い口の sfpAbsent など）で、表の終わりと
  みなして読むのをやめていた。その行より下の口は、線の上下が分からないままになっていた。
- 直したこと: 知らない言葉でも、先頭が Config にある口なら表は続いているとみなして次の行へ進む。その口の上下は
  決めない（言葉の意味を資料で確かめられていないため）。試験は `tests/test_linkstate.py`。
- 未確認のまま: connected / notconnect / err-disabled 以外の言葉（disabled、inactive、monitoring など）は既存の試験の形。

## Junos の show interfaces terse を途中で読みやめていた（2026-09-29 JST）

- 確かめた資料: Junos OS CLI Reference「show interfaces terse」の出力欄の説明と、Junos Software CLI Reference
  「show interfaces」の show interfaces terse のサンプル出力。
- 起きていたこと: サンプルには Link が `ready` の行（`st0.1  up  ready inet`）と、同じ論理口の2つ目のアドレスだけを
  字下げして並べた行（`10.0.0.16  --> 0/0`）がある。読み手は Admin と Link がどちらも up / down でない行を表の終わりと
  みなしていたので、その下の口（`ae0`、`ge-0/0/1` など）の上下が分からないままになっていた。
- 直したこと: 先頭がアドレス（IPv4・IPv6）か family の名前なら続きの行として飛ばす。Link が up / down 以外の行は
  上下を決めずに次へ進む。表が字下げされていても読む（字下げでは続きの行を見分けない）。`ae0.0` のような口の名前を
  アドレスと取り違えないよう、IPv4 は数字4つの形、IPv6 はコロンを含む形に絞った。試験は `tests/test_linkstate.py`。

## NX-OS の show interface brief も、知らない Status の言葉で読みやめる作りだった（2026-09-29 JST）

- 確かめた資料: Cisco Nexus 5000 / 5600 / 6000 Series Interfaces・Layer 2 Command Reference の show interface brief の出力例。
- 分かったこと: 公式の出力例の形（見出し2行、罫線、Status は up / down、`Eth1/5.2` の枝の口）には、読み手は合っていた。
  ただし Status が up / down 以外の行で表を読みやめる作りで、#103〜#105 と同じ形だった。公式の出力例には up / down
  以外の言葉は出ていないので、実機で出るかは確かめていない（未確認）。
- 直したこと: 出力例に無い言葉の行は上下を決めずに次へ進む（show interfaces status と同じ扱い）。試験は
  `tests/test_linkstate.py`。
- 残り: Cisco show ip interface brief、Aruba CX、PAN-OS、FortiGate、NEC QX、FastIron、アラクサラの表。

## show ip interface brief も、知らない Protocol の値で読みやめる作りだった（2026-09-29 JST）

- 確かめた資料: Cisco IOS Interface and Hardware Component Command Reference（IOS XE 16）の show ip interface と、
  その Table「show ip interface brief Field Descriptions」。
- 分かったこと: Status の値は up / down / administratively down の3つで、読み手はこれに合っていた。Protocol は値の
  一覧が無い。読み手は Protocol が up / down 以外の行で表を読みやめる作りで、#103〜#106 と同じ形だった。
- 直したこと: 先頭が Config にある口なら表は続いているとみなし、その口の上下は決めずに次へ進む。試験は
  `tests/test_linkstate.py`（試験の `down (err-disabled)` は、一覧に無い値の例として置いた。実機で出る形かは未確認）。
- 残り: Aruba CX、PAN-OS、FortiGate、NEC QX、FastIron、アラクサラの表。

## PAN-OS の show interface all を、最初の行で読みやめていた（2026-09-29 JST）

- 確かめた資料: Palo Alto Networks Knowledge Base の show interface all の出力（KCS kA14u000000ToqhCAC、kA14u000000sapmCAA、
  kA1Ki000000XZB5KAO）。
- 起きていたこと: state 欄は `1000/full/up` のほかに `ukn/ukn/down(autoneg)`、`ukn/ukn/down((autoneg))`、`[n/a]/[n/a]/up` の
  形で出る。読み手は `速さ/向き/up|down` の3つ区切りしか受け付けず、使っていない口（down(autoneg)）が1つでも先頭に
  あると、そこで表を読みやめていた。実機では未使用の口が先頭に並ぶことが多く、どの口の上下も分からないことがあった。
- 直したこと: 末尾の state（up / down と、その後ろの括弧）だけを見る。`[n/a]` の中の `/` に引きずられない。試験は
  `tests/test_linkstate.py`。
- 未確認のまま: Aruba CX の show interface brief（公式の出力例に当たれず、HPE のラボ手引きとコミュニティの作例だけ。
  読み手はそれらに合っていた）。新しい PAN-OS の `speed/duplex/state/fec` の形（公式の出力例に当たれていない）。

## FastIron の止めた口（Link が Disable）で、表の読み取りが止まっていた（2026-09-29 JST）

- 確かめた資料: Brocade FastIron Ethernet Switch Platform and Layer 2 Switching Configuration Guide の show interface brief の
  出力例、RUCKUS FastIron Management Configuration Guide 08.0.91 / 09.0.10 の show interfaces brief。
- 起きていたこと: 止めた口は Link 列に `Disable` と出る。読み手は `disabled` しか知らず、`Disable` の行で表を読みやめて
  いたので、その下の口の上下が分からないままになっていた。
- 直したこと: `Disable` を disabled と読む。知らない言葉でも Config にある口の行なら表は続ける（#103〜#108 と同じ扱い）。
  試験は `tests/test_linkstate.py`。
- 残り: FortiGate get system interface physical、NEC QX display interface brief、アラクサラ。

## linkstate.py の表の確かめ方の区切り（2026-09-29 JST）

- 公式の資料で確かめた表: Huawei VRP、Cisco show interfaces status、Cisco show ip interface brief、Junos terse、NX-OS brief、
  PAN-OS、FastIron（#103〜#109）。7つのうち6つに「知らない値の行で表を読みやめる」同じ形の不具合があった。
- 未確認のまま: Aruba CX と FortiGate（公式の出力例に当たれず、ラボ手引き・コミュニティ・第三者の作例とは合っていた）、
  NEC QX・NEC IX・アラクサラ（マニュアル置き場に届かない）、Arista の show ip interface brief、`show interfaces` の段落。
- 次から: 表を読む処理を新しく書くときは、知らない値の行を表の終わりとみなさない。先頭が Config にある口なら次の行へ進む。

## 機能の一覧（features.py）が、入っていない機能を載せていた（2026-09-29 JST）

- 確かめた資料: FortiOS CLI Reference の config firewall vip、Cisco IOS XE（Catalyst 9300）Stack Manager and High
  Availability Configuration Guide、Arista EOS User Manual、AOS-CX Multicast Guide、Junos OS の IGMP Snooping・PIM の資料。
- 起きていたこと:
  - `config firewall vip` があるだけで「負荷分散」と載せていた。vip は静的 NAT（ポート転送）にも使うので、NAT しか
    していない FortiGate にも負荷分散が載っていた。
  - `switch 1 provision` が1行あるだけで「スタック」と載せていた。単体の Catalyst にも1行書かれる（#90 と同じ見落とし）。
  - Arista の `pim ipv4 sparse-mode`、AOS-CX の `ip pim-sparse enable`、Junos の `set protocols pim` と
    `set protocols igmp-snooping` を拾えず、マルチキャストが「無い」ことになっていた。
- 直したこと: 負荷分散は `set type server-load-balance` の行で拾う。スタックは provision が2台分以上のときだけ載せる。
  3社の PIM と Junos の IGMP スヌーピングの行を足した。試験は `tests/test_features.py`（既存の FortiGate の試験は
  公式の形の vip に直した）。
- 同じ形の見落とし: 読み手（hapair.py など）で直した取り違えは、features.py の表にも同じ形で残っていないかを見る。

## 機能の一覧で、組む前の1台をスタックと読み、ヤマハ・PAN-OS の ACL・NAT を拾えていなかった（2026-09-29 JST）

- 確かめた資料: H3C「Automatic IRF Setup Configuration Examples」「IRF Configuration Examples」、Huawei CloudEngine
  8800/6800/5800 Configuration Guide - Virtualization「Example for Establishing a Stack of Two Switches」、ヤマハ
  「NAT ディスクリプター コマンド仕様」「SNMP」とコマンド設定例、Palo Alto Networks Knowledge Base「Viewing the
  configuration in set and XML format」「How to Create and View NAT Policies Using the CLI」、H3C「EAD Authentication (IPv6)
  Configuration Examples」。
- 起きていたこと:
  - `irf-port` か `stack-port` の行が1行でもあれば「スタック」と載せていた。組む前の1台の設定にも自分の番号の
    `irf-port 1/2`・`stack-port 1/1` が書かれるので、単体の機器がスタックに見えていた（#90・#92・#93 と同じ形）。
  - ヤマハの `ip filter`・`ip lan2 secure filter`・`nat descriptor`・`snmpv2c host`、PAN-OS の `set rulebase security` と
    `set rulebase nat`、H3C の `acl ipv6 advanced` を拾えず、ACL・NAT・SNMP が「無い」ことになっていた。
- 直したこと: `N/x` の N（メンバー番号）が2つ以上あるときだけスタックとする。4社分の行を足した。ヤマハの
  `snmp host none`（初期値。どこからも受け付けない）は SNMP としない。試験は `tests/test_features.py`。
- 未確認のまま: Huawei S シリーズ（V200）のスタックは設定が Flash だけに書かれ、Config に出ない（同社 Command
  Reference の display stack configuration の説明）。Config からは読めないので、読み手は足していない。

## 機能の一覧で、Cisco・H3C 以外の DHCP サーバーとリレーを拾えていなかった（2026-09-29 JST）

- 確かめた資料: ヤマハの設定例（rtpro.yamaha.co.jp）、FortiOS Administration Guide「Basic configuration」、Huawei
  S200/S300/S500/S600 Configuration Guide「Example for Configuring DHCPv4 Relay」、Juniper Support「[EX] Secondary TFTP
  cannot be configured in the same DHCP pool」、AOS-CX 10.12 IP Services Guide「Troubleshooting」。
- 起きていたこと: ヤマハの `dhcp service server`、FortiGate の `config system dhcp server`、VRP の `dhcp select global` と
  `dhcp relay server-ip`、Junos の `dhcp-local-server`、AOS-CX の `dhcp-server vrf` を拾えず、アドレスを配っている機器が
  「DHCP 無し」に見えていた。
- 直したこと: 5社分の行を足した。AOS-CX は塊の中の `enable` があるときだけ DHCP サーバーとする（enable の無い塊は配って
  いない）。試験は `tests/test_features.py`。
- 未確認のまま: Junos の DHCP リレー（`set forwarding-options dhcp-relay`）と PAN-OS の DHCP（`set network dhcp`）は、公式の
  資料に当たれず第三者の作例しか無かったので足していない。H3C の口の下の `dhcp select server` も未確認。

## 機能の一覧で、Junos の RSTP・SSH、Huawei の STelnet、PAN-OS の IPsec を拾えていなかった（2026-09-29 JST）

- 確かめた資料: Juniper「Example: Faster Convergence and Improved Network Stability with RSTP on EX Series Switches」、
  Juniper Support「[EX] How to enable remote management access to CLI on EX switches」、Huawei S300/S500/S2700/S5700/S6700
  V200R023C10 Configuration Guide の STelnet の例、Palo Alto Networks Knowledge Base「How to Configure an IPSEC VPN with
  Route and Tunnel Configuration from CLI」。
- 起きていたこと: `set protocols rstp`、`set system services ssh`、`stelnet server enable`、`set network tunnel ipsec` を
  拾えず、ループ防止・SSH・拠点間の暗号化が「無い」ことになっていた。
- 直したこと: 4つの行を足した。試験は `tests/test_features.py`。
- 未確認のまま: ヤマハの `sshd service on`（第三者の作例だけ）、Junos の `set protocols mstp` / `vstp`、H3C の
  `ssh server enable`。公式の資料に当たれていないので足していない。

## Catalyst 9000 の 2.5G 口が、略記と正式名で別の口になっていた（2026-09-30 JST）

- 確かめた資料: Command Reference, Cisco IOS XE 17.10.x（Catalyst 9300）の interface コマンド、Cisco「Migration Guide from
  Cisco Catalyst」Table 5。
- 起きていたこと: 口の名前をそろえる表（ifname.py）に `TwoGigabitEthernet`・`FiveGigabitEthernet` と略記 `Tw` が無かった。
  `show interfaces status` の `Tw1/0/1` と CDP・Config の `TwoGigabitEthernet1/0/1` が突き合わず、同じ口が別々に扱われていた。
- 直したこと: 3つを表に足した。25G の `Twe` とは別の口として扱う。試験は `tests/test_ifname.py`（新設）。
- 未確認のまま: 5G の `Fi`、100G の `Hu` の略記は公式の出力例に当たれていないので足していない。
- features.py の docstring に、確かめた行と未確認の行の区切りを書いて、機能の一覧の確認を閉じた。

## トンネルの読み手で、Junos の IP-IP を IPsec と読み、FortiOS の IPv6 の宛先を落としていた（2026-09-30 JST）

- 確かめた資料: Juniper「Chassis Cluster Redundant Ethernet Interfaces」の IP-over-IP Tunneling、FortiOS 6.4.7 / 7.0.1 /
  7.0.12 CLI Reference の config vpn ipsec phase1-interface。
- 起きていたこと:
  - Junos の `tunnel source / destination` を持つ口のうち、`gr-` 以外をすべて IPsec としていた。IP-IP は `ip-0/0/0` に
    載るので、IP-IP トンネルが IPsec と出ていた（IPsec の st0 は `tunnel source` を持たない）。
  - FortiOS の IPv6 の IPsec は `set remote-gw6` / `set local-gw6` に書くのに、IPv4 の行しか見ておらず、宛先が空、
    送信元が口の名前のままだった。
- 直したこと: Junos は `gr-` を gre、`ip-` を ipip とし、どちらでもない口は種別を空のままにする。FortiOS は IPv6 の行も
  読む。試験は `tests/test_topology_logical.py`。
- 未確認のまま: Cisco の `tunnel source / destination / mode / vrf`（公式の設定例に当たれず、第三者の作例だけ）。

## FortiOS の並べて書く値を、引用符の中の空白でも割っていた（2026-09-30 JST）

- 確かめた資料: FortiOS 8.0.1 Administration Guide「Explicit proxy authentication」（`set groups "NTLM-FSSO-Group"
  "Ldap-Group"`）、7.4.10「Internet service groups in policies」（`set name "PC to Google"`）、「Configuring FSSO firewall
  authentication」（引用符の無い `set srcaddr Windows_net`）。
- 起きていたこと: `blocks` が値の外側の引用符を外してから、`words` が空白で割っていた。`set srcaddr "Sales LAN"` は
  2つの名前と数えられ、NAT の対象が「名前ではない」扱いになっていた。`set service "Windows AD" "DNS"` も3つに割れていた。
- 直したこと: `blocks` が書いてあったままの値（`raw`）も持ち、並べて書く値は `items` で引用符の単位で分ける。
  `words` は呼ばれなくなったので消した。試験は `tests/test_fortitext.py`（新設）。
- inputtext.py（BOM・行末・タブを揃える）は機器の書式ではなくファイルの保存形式の話なので、公式の資料の対象外とした。

## VyOS 1.3 以降の LAG の子を読めていなかった（2026-09-30 JST）

- 確かめた資料: VyOS 1.5.x (circinus) LTS Documentation「Bond / link aggregation」と rolling release の同じ章。
- 起きていたこと: 子の口の側に書く旧来の `set interfaces ethernet eth0 bond-group bond0`（EdgeOS もこの形）しか読んで
  いなかった。VyOS 1.3 以降は束ねる口の側に `set interfaces bonding bond0 member interface eth0` と書くので、LAG の子が
  どこにも付かなかった。
- 直したこと: `member interface` の行も読み、子の口に束ねる番号を付ける。旧来の形も引き続き読む。試験は
  `tests/test_configparse_vyos.py`。
- 未確認のまま: vyos.py のほかの行（address・vif・description・disable・static route）。

## EXOS で、番号で指定した VLAN と description-string を読めていなかった（2026-09-30 JST）

- 確かめた資料: ExtremeXOS Command Reference Guide 22.1 / 31.7、Switch Engine v33.2.1 Command References の configure vlan
  add ports、ExtremeXOS Command Reference Guide 30.1 の一覧（configure port description-string）。
- 起きていたこと:
  - `configure vlan 10,20 add ports 5 tagged` のように VLAN を番号の並びで書くと（ExtremeXOS 16.1 から）、名前で番号を
    引けず、口が1つも付かなかった。
  - `configure ports 1 description-string "..."`（長い説明）を読まず、`display-string`（20字までの表示名）だけを説明に
    していた。
- 直したこと: 番号の並びはそのままほどいて使う。`description-string` を読み、両方あれば長い方を説明に持つ。試験は
  `tests/test_configparse_exos.py`。

## FastIron で、VLAN に載せた LAG を読めていなかった（2026-09-30 JST）

- 確かめた資料: RUCKUS Support Knowledge Base 000015488「Configuring MCT Cluster」（`tagged lag 2`）。口と LAG を1行に
  並べる形は FastIron 10.0.10 L2 ガイドの MCT の設定例（公式ではない写しで読んだ）。
- 起きていたこと: VLAN の下のメンバーを `tagged ethe ...` の形でしか読んでおらず、`tagged lag 2` の行を捨てていた。
  LAG で上位と繋ぐ構成では、束ねた口に VLAN が1つも付かなかった。
- 直したこと: `lag N`（`lag N to M` も）を読み、束ねの定義（`lag "名前" dynamic id N` の下の `ports`）から口に
  ほどいて VLAN を付ける。試験は `tests/test_configparse_fastiron.py`。
- Arista（arista.py）は、`ip route` の name / tag の後ろの値を口や距離として読んでいないかを見た。IOS の読み手が先に
  正しく読んでいて不具合は無かったので、変えていない。EOS の公式の資料（arista.com）にはこの環境から届かず、
  BGP の maximum-routes の既定値（12000）と peer group の扱いは未確認のまま。

## FortiGate のダイヤルアップ・IPv6・DDNS の IPsec に「このままでは上がりません」と出ていた（2026-09-30 JST）

- 確かめた資料: FortiOS 6.4.7 / 7.0.12 CLI Reference の config vpn ipsec phase1-interface（type [static|dynamic|...]、
  remote-gw6、remotegw-ddns）。
- 起きていたこと: 相手を `remote-gw` からしか読んでいなかった。IPv6（`remote-gw6`）、名前で引く相手（`remotegw-ddns`）、
  相手を決めないダイヤルアップ（`set type dynamic`）では相手が空になり、点検が「相手（set peer）が書かれていません。
  このままでは上がりません」（HIGH）と誤って指摘していた。直す前のコードで出ることを確かめた。
- 直したこと: 相手を remote-gw → remote-gw6 → remotegw-ddns の順に読み、ダイヤルアップは相手を「dynamic」とする。
  試験は `tests/test_configparse_nat_dhcp_ipsec_vendors.py`（点検に指摘が出ないことまで見る）。
- 同じ形の見落とし: #117 の tunnels.py（FortiOS の IPv6 の宛先）と同じ。phase1-interface を読む処理は2か所ある。

## ダイナミッククリプトマップを指す行に「このままでは上がりません」と出ていた（2026-09-30 JST）

- 確かめた資料: Cisco「IPsec VPN Command Reference」の crypto map (global configuration) と set peer、Cisco Secure
  Firewall ASA VPN CLI Configuration Guide 9.23「Create a Crypto Map Entry to Use the Dynamic Crypto Map」。
- 起きていたこと: `crypto map OUTSIDE 65535 ipsec-isakmp dynamic DYN` のような、相手の分からない接続を受けるための
  行は相手を書かない（書けない）のに、点検が「相手（set peer）が書かれていません。このままでは上がりません」（HIGH）と
  出していた。ASA・IOS の両方で出ることを直す前のコードで確かめた。IOS の読み手は `dynamic 名前` を読んでもいなかった。
- 直したこと: IOS の読み手が `dynamic 名前` を読む（種別 `ipsec-isakmp dynamic`、指す先を profile に持つ）。点検は
  種別に dynamic を含む行には相手を求めない。試験は `tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- 同じ形の見落とし: #122（FortiGate のダイヤルアップ）と同じ。「相手が無い」を指摘する前に、相手を決めない形かを見る。

## Huawei のテンプレートから作る IPsec に「このままでは上がりません」と出ていた（2026-09-30 JST）

- 確かめた資料: Huawei AR100-S〜AR3200-S V200R010 CLI-based Configuration Guide - VPN「Configuring an IPSec Policy Using an
  IPSec Policy Template」、NetEngine AR1000V V300R021 の「Example for Establishing an IPSec Tunnel ... Using an IPSec Policy
  Template」。
- 起きていたこと: `ipsec policy policy1 10 isakmp template use1`（相手のアドレスが決まっていない拠点を受ける本社側の形）の
  `template use1` を読まず、相手も方式も範囲も空になり、点検が「このままでは上がりません」（HIGH）と出していた。
  直す前のコードで出ることを確かめた。
- 直したこと: `template 名前` を読み、種別を `isakmp template`、指す先を profile に持つ。点検は種別に template を含む行
  にも相手を求めない。試験は `tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- 次に見るもの: Junos の `set security ike gateway ... dynamic`（相手のアドレスを書かない gateway）も同じ形の見込み。

## Junos の相手のアドレスを書かない IKE gateway に「このままでは上がりません」と出ていた（2026-09-30 JST）

- 確かめた資料: Juniper「Example: Configuring Dynamic VPN」、Juniper Support「[SRX] Example - How to configure a dialup
  IPSec VPN with group IKE ID」。
- 起きていたこと: `set security ike gateway GW dynamic hostname ...`（相手のアドレスが変わる拠点を名前で受ける形）では
  `address` の行が無く、VPN の相手が空になり、点検が「このままでは上がりません」（HIGH）と出していた。直す前のコードで
  出ることを確かめた。
- 直したこと: `dynamic` の行を持つ gateway は相手を「dynamic」とする（#122 の FortiGate と同じ扱い）。試験は
  `tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- これで「相手を決めない形」の誤りは FortiGate（#122）・Cisco ASA/IOS（#123）・Huawei（#124）・Junos を直した。NEC IX は
  資料に届かず未確認。

## FortiGate の DHCP サーバーを3か所で読み違えていた（2026-09-30 JST）

- 確かめた資料: FortiOS CLI Reference「config system dhcp server」（7.0.1 / 7.4.0）、「config system dns」（7.6.3）。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - `set status disable`（使わない設定。既定は enable）のサーバーも、配っていることにしていた。
  - `config exclude-range` の `edit 1` を DHCP サーバーの `edit` と取り違え、あとの `config ip-range` を別のサーバーの
    範囲として覚えていた。配る範囲が空になり、配らない範囲も読んでいなかった。
  - `set dns-service local`（ポート自身のアドレスを配る）と `default`（FortiGate 自身の DNS を配る）は dns-server を
    書かない形なのに、「名前解決の先を配っていません」と出していた。既定は specify。
- 直したこと: disable のサーバーは読まない。入れ子の `config` を積んで数え、ip-range と exclude-range を分けて読む
  （exclude-range は配らない範囲へ）。local はポートのアドレス、default は `config system dns` の primary / secondary を
  配る先とする。試験は `backend/tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- 同じ形の見落とし: 「書かない形」は既定値と別の値の組み合わせで出る。読み手は既定値を資料で確かめてから空を判定する。

## Cisco の DHCP で、上流から受け取る DNS と親のプールを受け継ぐ固定割り当てを「配っていない」と言っていた（2026-09-30 JST）

- 確かめた資料: Cisco「IP Addressing Configuration Guide, Cisco IOS XE 17.x」の「Configuring the Cisco IOS XE DHCP Server」
  （DHCP Attribute Inheritance、Configuring Manual Bindings、Configuring a Remote Device to Import DHCP Server Options）と
  「Configuring the DHCP Server On-Demand Address Pool Manager」（VRF のプールは受け継がない）。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - `import all` で DNS を上流から受け取って配るプールに「名前解決の先を配っていません」と出していた。
  - 固定割り当て（`host 10.0.0.20 255.255.255.0`）のプールは、そのアドレスを含む network のプールから既定ゲートウェイと
    DNS を受け継ぐのに、「既定ゲートウェイを配っていません」（HIGH）と出していた。
- 直したこと: `import all` / `import interface` を読み、DNS の指摘を出さない。`host` を読み、配るのはその1つとする。
  書いていない値は、アドレスを含むいちばん狭い network のプールから受け継ぐ（VRF のプールは受け継がない）。
  試験は `backend/tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- 未確認: `host` でマスクを省いたときの扱いは資料に書かれていない。親のプールの network を借りる形にした。

## Huawei の口の上の DHCP を読まず、ゲートウェイを配ると言っていた（2026-09-30 JST）

- 確かめた資料: Huawei「S9300 and S9300X V200R025C00 Configuration Guide - IP Service」の「Configuring a DHCP Server」、
  「FAT AP / Cloud AP V200R023C00 コマンドリファレンス」（中国語版）の「dhcp server gateway-list」、
  「S200, S300, S500, and S600 V600R025C10 Configuration Guide」の「Example for Configuring a DHCPv4 Server Based on an
  Interface Address Pool」、S シリーズ Troubleshooting Guide（V600）の DHCP の FAQ。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - `ip pool` の gateway-list のアドレスは機器が自動で配らない側に入れるのに、excluded-ip-address に書いていないと
    「ゲートウェイのアドレスも配ってしまいます」と出していた。
  - 口の下の `dhcp select interface`（口のアドレスの区画を配る形）を読んでいなかった。`dhcp server gateway-list` を
    書かなければ、VRRP の仮想アドレス（無ければ口のアドレス）をゲートウェイとして配る。
  - `dhcp select relay` と `dhcp relay server-ip`（他所のサーバーへ回す形）を読んでいなかった。
- 直したこと: プールに `gateway_reserved`（ゲートウェイは自動で配らない）を持たせ、点検はこれを見る。口の下の DHCP を
  「interface 口の名前」のプールとして読み、リレーは relays へ入れる。書いた excluded はそのまま残し、自動の分は混ぜない。
  試験は `backend/tests/test_configparse_nat_dhcp_ipsec_vendors.py`。

## Junos の古い形の DHCP を読まず、上流から受け取る DNS を「配っていない」と言っていた（2026-09-30 JST）

- 確かめた資料: Juniper「DHCP Attributes for Address-Assignment Pools」（propagate-settings）、「Legacy DHCP and Extended
  DHCP」（Table 5 と階層の受け継ぎの注記）、「DHCP Server Options」の Configuring Address Pools。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - `dhcp-attributes propagate-settings 口`（上流の DHCP で受け取った DNS などを配る）のプールに「名前解決の先を
    配っていません」と出していた。
  - 古い形（`set system services dhcp pool 10.3.3.0/24 ...`）を読んでいなかった。全体（`system services dhcp`）に
    書いた name-server などは、プールで書かなければ受け継ぐ。
- 直したこと: propagate-settings を読んで DNS の指摘を出さない。古い形のプールを読み、全体の値を受け継ぐ。範囲を
  書かなければ口自身のアドレスは配られないので、ルーターが口自身のアドレスなら「ゲートウェイも配る」と言わない。
  試験は `backend/tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- 未確認: H3C（Comware）の `dhcp server ip-pool` は h3c.py では読んでいない（NEC QX の読み手は読む）。ゲートウェイを
  自動で除くかは、次の周で資料に当たる。

## H3C の読み手が DHCP を読んでいなかった（2026-09-30 JST）

- 確かめた資料: H3C「05-DHCP configuration」（Comware 7、2025-09 版）の Configuring an IP pool と Configuring the DHCP
  relay agent、「17-DHCP Quick Start Configuration Guide」。
- 起きていたこと: h3c.py は `dhcp server ip-pool` / `ip pool` を読まず、DHCP の点検と一覧から H3C の配布が抜けていた
  （NEC QX の読み手は読んでいた）。直す前のコードで試験が落ちることを確かめた。
- 直したこと: 2つのプールの書き方、`network` の `mask 255.255.255.0` と `24` の2つの書き方、`address range`、
  `gateway-list`・`dns-list`・`domain-name`・`expired`、配らないアドレス（プールの `forbidden-ip` は並べる形、
  `forbidden-ip-range` と全体の `dhcp server forbidden-ip` は範囲）、口の下の `dhcp relay server-address` を読む。
  資料に「DHCP サーバー自身のアドレスは配らない」とあるので、ゲートウェイが機器自身のアドレスなら「ゲートウェイも
  配る」と言わない（Huawei と違い、gateway-list を自動で除くとは書かれていない）。試験は
  `backend/tests/test_configparse_nat_dhcp_ipsec_vendors.py`。
- 気付いたが手を付けなかったこと: NEC QX の読み手は `forbidden-ip A B` を範囲として読んでいる。H3C の資料では並べる形。
  QX の資料には届かないので未確認のまま。

## VRRP のアドレスの持ち主に「優先度が同じです」と出ていた（2026-10-01 JST）

- 確かめた資料: Cisco「Network Services Configuration Guide - Configuring VRRP」（IOS XE）の VRRP Router Priority and
  Preemption と Configuring VRRP Object Tracking、Cisco IOS First Hop Redundancy Protocols Command Reference の
  standby priority。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - 仮想アドレスが自分の口のアドレスと同じ機器（持ち主）は優先度が 255 に固定されるのに、両方とも priority を
    書いていないと「優先度が同じです」（MEDIUM）と出していた。
  - priority を書かないとき（None）と既定の 100 を明示したときを別の値として比べ、同じ優先度を見逃していた。
- 直したこと: 比べる前に実際に効く優先度に直す（書かなければ 100、VRRP の持ち主は 255）。根拠の行には既定なら
  「（既定）」と添える。試験は `backend/tests/test_l3review.py`。
- hareview も見た。役割・優先度とも両方に書いてある値だけで判定しており、書かない形の誤りは無かった。
- 気付いたが手を付けなかったこと: VRRP で優先度が同じときは住所の大きい側が主になる（同じ資料）。今の文言
  「起動の順番で決まります」は HSRP には合うが VRRP には合わない。

## redistribute connected の機器に「外へ広告されていないセグメントがあります」と出ていた（2026-10-01 JST）

- 確かめた資料: Cisco「Redistributing Connected Networks into OSPF」「Redistribute Connected Networks into OSPF with
  Subnet Keyword」、Cisco「Network Services Configuration Guide, Cisco IOS XE 17.x - Configuring HSRP」と
  「Network Services Configuration Guide - Configuring VRRP」の Priority and Preemption。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - `redistribute connected` は network 文に入っていない区画もまとめて広告するのに、読んだ redistribute を見ずに
    「外へ広告されていないセグメントがあります」（MEDIUM）と出していた。
  - 「優先度が同じ」の説明が HSRP と VRRP で同じ「起動の順番で決まります」だった。HSRP は先に上がった側が居座り、
    VRRP は口のアドレスの大きい側が主になる。
- 直したこと: OSPF・BGP・EIGRP などのどれかに `redistribute connected` があれば、広告の指摘を出さない。説明は
  HSRP と VRRP で分けた。試験は `backend/tests/test_l3review.py`。
- 未確認: Huawei の `import-route direct`、Junos の direct の書き出しも同じ意味の見込みだが、資料に当たっていない
  ので扱いを変えていない。
- l3review の残り（既定の経路・経路の向き先・同じ重み・passive）も読んだ。既定の経路は OSPF / BGP があれば言わず、
  同じ重みは distance を書かなければ 1 として比べており、書かない形の誤りは見つからなかった。

## Huawei の import-route direct を読まず、「外へ広告されていないセグメント」と出ていた（2026-10-01 JST）

- 確かめた資料: Huawei「S9300 and S9300X V200R024C00 Command Reference」の「import-route (OSPF)」（`direct` は直結の
  経路を取り込む。管理口の区画まで取り込まれる、との注意書きつき）。
- 起きていたこと: undo 書式（Huawei・H3C・NEC QX）の OSPF の読み手が `import-route` を読まず、l3review も `direct` を
  広告として数えていなかった。`import-route direct` の機器に「外へ広告されていないセグメントがあります」（MEDIUM）と
  出ていた。直す前のコードで試験が落ちることを確かめた。
- 直したこと: OSPF の `import-route 種類` を redistributes に入れ（`limit` は種類ではないので除く）、l3review は
  `direct` も `connected` と同じく広告として数える。試験は `backend/tests/test_l3review.py`。
- 未確認のまま: Huawei の BGP の `import-route`（資料に当たっていない）と、Junos の export policy の
  `from protocol direct`（policy の中身を読んで当て先まで結ぶ必要があり、今回は手を付けていない）。

## FastIron に、書き方の無い「spanning-tree mode」を書けと言っていた（2026-10-01 JST）

- 確かめた資料: Ruckus「FastIron 08.0.60 Layer 2 Switching Configuration Guide」の「Enabling or disabling the Spanning
  Tree Protocol (STP)」、HPE Aruba「AOS-S 16.11 Advanced Traffic Management Guide」の「Configuring MSTP at a glance」。
- 起きていたこと: configcheck の「STP のモードを決めていません」は機種を見ず、`spanning-tree` の行があって
  `spanning-tree mode` の行が無いと出していた。FastIron は STP の種類を `spanning-tree`・`spanning-tree single`・
  `spanning-tree 802-1w` で選び、`spanning-tree mode` の書き方が無い。直す前のコードで試験が落ちることを確かめた。
- 直したこと: `spanning-tree mode` の書き方が無い機種（FastIron）には言わない。AOS-S には `spanning-tree mode mstp`
  があるので、そのまま言う。試験は `backend/tests/test_configcheck.py`。
- configcheck の残り（SSH の版、ポートセキュリティの violation、トランクのタグ無し VLAN 1 など）は、既定値のまま
  書かないこと自体を指摘する作り。SSH の版は docstring に出典がある。violation の既定（shutdown）は次の周で
  Security Configuration Guide, Cisco IOS XE 17.16.x の Default Port Security Configuration で確かめ、出典を足した。

## linkcheck で、speed auto を「片側だけ速度を固定」、ip ospf authentication null を平文の認証と読んでいた（2026-10-01 JST）

- 確かめた資料: Cisco「Cisco IOS XE 17 - Interface Characteristics Configuration Guide」の Default Ethernet interface
  configuration（Speed / Duplex mode: Autonegotiate）、「Cisco Nexus 6000 Series NX-OS Unicast Routing Command
  Reference」の ip ospf authentication（null は認証を掛けない）、技術資料「Configure Authentication in Open Shortest
  Path First」。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - 片側が `speed auto` / `duplex auto`（既定の自動を書いたもの）、片側が何も書いていないと、「片側だけ速度を固定
    しています」「片側だけ二重を固定しています」（MEDIUM）と出していた。
  - `ip ospf authentication null` の `authentication` を見て、平文の認証（text）と読んでいた。相手が何も掛けていない
    と「OSPF の合言葉の掛け方が両端で違います」（HIGH）になる。
- 直したこと: linkcheck は `auto` を書いていないのと同じに扱う。Cisco の読み手は `null` を「none」（掛けていない）と
  読む。根拠の行では none も「掛けていない」と出す。試験は `backend/tests/test_linkcheck.py`。
- linkcheck の残り（トランクの許可 VLAN、タグ無し VLAN、アドレス、OSPF の hello / dead / network、MTU、束ね）は、
  片側が書いていなければ言わない作りで、書かない形の誤りは無かった。

## Cisco の ip ospf authentication key-chain を平文と読み、Huawei の BGP の import-route を読んでいなかった（2026-10-03 JST）

- 確かめた資料: Cisco「IP Routing Configuration Guide, Cisco IOS XE 17.x - OSPFv2 Cryptographic Authentication」、
  Huawei「S7700 V200R023C10 Command Reference」の「import-route (BGP)」。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - `ip ospf authentication key-chain 名前`（鍵の束による暗号の認証、HMAC-SHA）を、`message-digest` が無いので
    平文（text）と読んでいた。相手が平文の認証だと隣にならないのに、linkcheck は「同じ」と見ていた。
  - undo 書式の BGP の読み手が `import-route` を読まず、`import-route direct` の機器に「外へ広告されていない
    セグメントがあります」と出ていた。
- 直したこと: key-chain は「key-chain」として読む（平文とも MD5 とも別）。BGP の `import-route` を redistributes に
  入れる。字下げを外した行で読むので、`ipv4-family` / `ipv6-family` の見出しで家族を切り替え、IPv4 の unicast
  （と BGP の直下）のものだけ数える。試験は `backend/tests/test_linkcheck.py`、`backend/tests/test_l3review.py`。
- 未確認のまま: NEC QX の `forbidden-ip A B` が範囲か並べる形か（NEC の資料にこの環境から届かない）。

## 点検ごとに既定値を書き直していたのを、defaults.py に集めた（2026-10-03 JST）

- 起きていたこと: 既定値（HSRP・VRRP の優先度、静的経路の距離、速度・二重の自動、OSPF の認証なし）を、l3review と
  linkcheck がそれぞれの中で書いていた。同じ 100 が2か所にあり、出典も点検ごとに散っていた。9 月後半の誤った指摘の
  多くは「既定値を確かめずに空を判定した」形で、既定値の置き場所が決まっていないことが根にあった。
- 直したこと: `backend/app/services/defaults.py` に既定値と出典を集め、点検はそこから引く。振る舞いは変えていない
  （既存の試験がそのまま通る）。どの点検がどの既定値に頼っているかは docs/knowledge/defaults.md に表にした。
  静的経路の距離の既定 1 は、今回 Cisco の資料（Basic IP Routing の Table 1）で確かめて出典を足した。
- 次から: 既定値に頼る点検を書くときは、先に defaults.py に定数と出典を置く。

## Huawei・H3C・FortiGate の静的経路の既定の距離を Cisco の 1 として比べ、tag の値を距離と読んでいた（2026-10-03 JST）

- 確かめた資料: Huawei「SME Network Fat AP and Cloud AP V200R025C10 Command Reference」の ip route-static と
  「S7700 V200R023C10 Command Reference」の ip route-static default-preference、H3C「02-Static routing commands」
  （Comware 7）、FortiOS CLI Reference「config router static」（7.0.1）。
- 起きていたこと（どれも直す前のコードで試験が落ちることを確かめた）:
  - l3review「同じ重みで複数あります」は、書いていない距離を機種を問わず 1 として比べていた。Huawei・H3C の既定は 60、
    FortiGate の既定は 10 なので、片側だけ既定値を明示した組（`preference 60` と書かない経路）を見逃していた。
  - undo 書式の `ip route-static ... tag 100` の 100 を距離、`tag` を口の名前と読んでいた。`description` の後ろの語や
    `track`・`bfd` の値も同じく取り違えていた。
- 直したこと: defaults.py に機種ごとの静的経路の既定（Huawei・H3C 60、FortiGate 10）を置き、l3review は機種を渡して
  引く。undo 書式の読み手は、値を伴う語（preference・tag・track・backup-nexthop・backup-interface・bfd）を語ごとに
  読み、description の後ろは読まない。試験は `backend/tests/test_l3review.py`、`backend/tests/test_defaults.py`。
- 未確認のまま: Junos の static の preference、NEC QX の既定。`ip route-static default-preference` で既定を変えた機器。

## Junos の静的経路の preference と qualified-next-hop を読んでいなかった（2026-10-03 JST）

- 確かめた資料: Juniper「Configure Static Routes」（静的経路の既定の preference は 5）、「Static Route Preferences and
  Qualified Next Hops」。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Junos の読み手は `route X next-hop Y` だけを読み、
  経路の `preference` も、待機の経路を書く `qualified-next-hop Y preference Z` も読んでいなかった。待機の経路は経路の
  一覧から抜け、l3review「同じ重みで複数あります」は Junos の既定 5 と書いた 5 の組も見逃していた。
- 直したこと: 経路の preference はその経路の next-hop すべてに、qualified-next-hop の preference はその転送先だけに
  当てる。defaults.py に Junos の既定 5 を足した。試験は `backend/tests/test_l3review.py`。
- 未確認のまま: `static defaults preference` で既定を変えた機器。中括弧の書式（set 形式でない Config）の静的経路は、
  前と同じく読んでいない。

## Aruba CX・Arista・ヤマハ・NEC IX の静的経路の距離を見た（直すものは無かった）（2026-10-03 JST）

- 確かめた資料: HPE Aruba Networking「AOS-CX 10.13 IP Routing Guide」（8400）の ip route distance（既定 1、
  `distance N` と書く）と ip route tag。Arista は EOS User Manual にこの環境から届かず、Arista が出している Ansible の
  arista.eos コレクションの eos_static_routes で書き方（数字だけが距離、tag・name は値を伴う）を見た。
- 見たこと: Aruba CX と Arista の `ip route` は、先に IOS の読み手が拾い、その読み手は tag・name・distance を値を伴う語と
  して読んでいた。機種ごとの読み手は同じ経路を読み飛ばすので、`tag 10` を距離と読む形は表に出ない。試験を足したが、
  直す前のコードでも通ったので取り下げた（作り話の改善をしない）。
- ヤマハの metric は経路のメトリックで距離ではない（読み手の docstring のとおり）。NEC IX は `distance N` を読んでいる。
- 残り: 機種ごとの読み手（arista.py・aruba.py の _parse_routes）には、数字なら何でも距離と読む書き方が残っている。いまは
  IOS の読み手が先に拾うので害は無いが、IOS の読み手が拾えない書き方が来たときに表に出る。
- 未確認: NEC IX・NEC QX・Arista の静的経路の既定の距離（資料に届かない）。

## 既定値に拠った指摘に、拠った資料の名前が出ていなかった（2026-10-03 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 書いていない設定を既定値で読んだ指摘
  （静的経路の同じ重み、HSRP/VRRP の同じ優先度、片側だけ速度固定、OSPF の認証の食い違い）は、根拠の行に
  「既定」と出るだけで、その既定をどの資料で確かめたかが画面にも API にも出ていなかった。読み手は確かめに行けない。
- 直したこと: `Finding.sources` を足し、資料の名前を defaults.py に既定値と並べて置いた。既定値に拠ったときだけ
  付け、資料に届いていない機種は「未確認」と出す。L3 と結線の点検の API と画面に出した。設計は
  `docs/knowledge/finding-sources.md`。
- 残り: Config 点検ほか、他の点検の指摘にはまだ付けていない。

## Config 点検の既定値に拠った指摘にも、資料の名前が出ていなかった（2026-10-03 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Config 点検の、既定の動きを前提にした指摘
  （アクセスの口の既定 VLAN 1、DHCP・ARP の見張りで口が既定で信用されない、option 82 の挿入が既定で有効、HSRP の
  既定の版1、ポートセキュリティの violation の既定 shutdown）は、出典がコードのコメントにしか無く、画面にも API にも
  出ていなかった。
- 直したこと: 資料の名前を defaults.py に置き（`*_SOURCE`）、6つの指摘に付けた。`POST /config-check` と Config 点検の
  画面に出した。試験は `backend/tests/test_configcheck.py` の末尾。
- 付けなかったもの: STP のモード（既定のモードを資料で確かめていない）、電話の口のポートセキュリティの上限（既定の 1 を
  既定値表で確かめていない）。

## STP の既定のモードとポートセキュリティの既定の上限を資料で確かめ、出典を付けた（2026-10-03 JST）

- 確かめた資料: Cisco「Layer 2 Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9300）」の Configuring Spanning Tree
  Protocol の Default Spanning-Tree Configuration（Spanning-tree mode: Rapid PVST+ (PVST+ and MSTP are disabled.)）。
  Cisco「Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9500）」の Port Security の Default Port Security
  Configuration（Maximum number of secure MAC addresses per port: One address）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「STP のモードを決めていません」「電話をつなぐ口で、
  覚えられる数が足りません」は既定に拠った指摘なのに、出典が出ていなかった。
- 直したこと: STP のモードは機種が Cisco IOS のときだけ出典を付ける（他の機種の既定は確かめていない）。電話の口は、
  上限を書いていない口があるときだけ付ける。試験は `backend/tests/test_configcheck.py` の末尾。
- 未確認: NX-OS・Arista ほか IOS 以外の STP の既定のモード。

## ASA で同じ security-level の口を通す指定を書いても「同じ数字どうしは通れない」と言っていた（2026-10-03 JST）

- 確かめた資料: Cisco「CLI Book 1: Cisco ASA Series General Operations CLI Configuration Guide, 9.16」の Routed and
  Transparent Mode Interfaces（By default, interfaces on the same security level cannot communicate with each other。
  `same-security-traffic permit inter-interface` で通す。低い security-level から高い側へは ACL で通さないかぎり通らない）。
  あわせて NX-OS（Nexus 9000 Layer 2 Switching Configuration Guide 10.2(x): 既定は Rapid PVST+）と Arista（EOS User
  Manual「Spanning Tree Protocol」: MSTP is the default STP version）の STP の既定のモードを確かめた。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: asareview の「security-level が同じ口が … つあります」は、
  `same-security-traffic permit inter-interface` を書いてある機器にも出ていた。通れる設定なのに「通れません」と言っていた。
  ASA の2つの指摘と、NX-OS・Arista の「STP のモードを決めていません」には、拠った資料の名前も出ていなかった。
- 直したこと: asareview に元の Config を渡し、`same-security-traffic permit inter-interface` があれば同じ数字の指摘を出さない。
  ASA の2つの指摘に出典を付け、STP のモードの出典は機種ごとに引く（`defaults.stp_mode_source`）。試験は
  `backend/tests/test_configparse_asa.py`、`backend/tests/test_configcheck.py` の末尾。
- 未確認: Aruba CX・Dell OS10 ほかの STP の既定のモード。

## 既定を変える書き方を読まずに出していた指摘（eBGP の直結確認、動的経路での既定経路）（2026-10-03 JST）

- 確かめた資料: Cisco「Cisco IOS IP Routing: BGP Command Reference」の neighbor disable-connected-check（1ホップの eBGP は
  既定で直結を確かめる。ループバックと張るときに外す）と neighbor ttl-security（`ttl-security hops N`。ebgp-multihop とは
  同時に書けない）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: update-source を指定した eBGP の相手に、
  `disable-connected-check` や `ttl-security hops` を書いてあっても「このままでは接続が張れません」と言っていた。
  「既定の経路が見当たりません」は OSPF・BGP のときだけ控え、EIGRP・RIP で既定の経路を受け取る機器にも出していた。
- 直したこと: 読み手で2つの書き方を読み（peer-group からも引き継ぐ）、点検はどれかがあれば言わない。指摘に出典を付けた。
  既定の経路の指摘は、動的な経路のプロトコルが1つでもあれば控える。試験は `backend/tests/test_policyreview.py`、
  `backend/tests/test_configcheck.py` の末尾。確かめた範囲の一覧は `docs/knowledge/finding-sources.md`。

## Arista にも「相手次第でトランクになります」と言っていた（2026-10-03 JST）

- 確かめた資料: Arista「EOS User Manual」の Virtual LANs (VLANs)（Ethernet and port channel interfaces are configured as
  switched ports by default。表示例は Administrative Mode: static access。switchport mode は access・trunk・dot1q-tunnel で、
  DTP の記載が無い）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: configcheck の「VLAN だけ書いて mode を書いていない口が、
  相手次第でトランクになります」は NX-OS だけを外していて、EOS の Config にも出ていた。EOS の口は既定でアクセスなので、
  直す必要の無いものを直させる指摘だった。
- 直したこと: 既定の dynamic auto を資料で確かめた Cisco IOS のときだけ言う。指摘に出典を付けた。試験は
  `backend/tests/test_configcheck.py` の末尾。configcheck の他の既定に拠る指摘は、既定を変える書き方を読んでいた
  （一覧は `docs/knowledge/finding-sources.md` の末尾の表）。

## Allied に「タグ無しの VLAN を、許可の一覧から外しています」と言っていた（2026-10-03 JST）

- 確かめた資料: Allied Telesis「VLAN Feature Overview and Configuration Guide」C613-22001-00 REV P の Setting native VLAN
  in trunk mode（By default, a Trunk mode switch port's native VLAN … is VLAN 1. … The new native VLAN must already be
  allowed for this switch port.）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Cisco では許可の一覧に無い VLAN はタグ無しでも通らないので
  指摘は正しいが、AlliedWare Plus の既定のタグ無し VLAN 1 は一覧に入れなくても一員になる。Allied の fixture
  （`allowed vlan add 10,20` と `native vlan 1`）に、直す必要の無い HIGH の指摘を出していた。
- 直したこと: Allied でタグ無し VLAN が 1 のときは言わない。試験は `backend/tests/test_configcheck.py` の末尾。
  他社の fixture で流した結果は `docs/knowledge/finding-sources.md` の末尾の表。
- 未確認: Dell OS10・Cisco SB・Alaxala（fixture が無い）。Allied の全体の BPDU guard の書き方。

## Allied の全体の BPDU guard（spanning-tree portfast bpdu-guard）を読んでいなかった（2026-10-03 JST）

- 確かめた資料: Allied Telesis「STP Feature Overview and Configuration Guide」C613-22026-00 REV A の Step 4. Configure BPDU
  Guard（グローバル設定で `spanning-tree portfast bpdu-guard`）と Spanning Tree modes（By default, the device operates in
  RSTP mode.）。PDF は WebFetch では 403 なので、curl で取得して pypdf で読んだ。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: configcheck の全体の BPDU guard の判定は Cisco・VRP・
  Aruba CX の書き方だけで、AlliedWare Plus のグローバルの1行を拾わず、守ってある端末口に「BPDU guard が入っていません」と
  言っていた。
- 直したこと: `_GLOBAL_BPDU_GUARD` に Allied の書き方を足した。「STP のモードを決めていません」に Allied の既定（RSTP）の
  出典を足した。試験は `backend/tests/test_configcheck.py` の末尾。

## 電話の口で VLAN ごとの上限だけを書いた口を見逃していた（2026-10-03 JST）

- 確かめた資料: Cisco「Security Configuration Guide, Cisco IOS XE 17.17.x（Catalyst 9500）」の Port Security の設定例（set the
  total maximum number of secure addresses to 20 (10 for data VLAN and 10 for voice VLAN)。`switchport port-security
  maximum 20` と `maximum 10 vlan access`・`maximum 10 vlan voice`）と、voice VLAN の口は上限を2にする旨のガイドライン。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: configcheck は `switchport port-security maximum` で始まる最初の
  行の末尾を上限と読んでいた。`maximum 1 vlan voice` のような VLAN ごとの行が先にあると末尾が `voice` になって数として読めず、
  口全体の上限が既定の1のままでも「覚えられる数が足りません」を出さなかった。
- 直したこと: 口全体の上限は `maximum N` だけの行から読み、無ければ既定の1とする。試験は `backend/tests/test_configcheck.py` の末尾。

## Arista の静的経路の距離の出典を「未確認」と出していた（2026-10-04 JST）

- 確かめた資料: Arista「EOS User Manual」の IPv4 の ip route（By default, the administrative distance assigned to static routes
  is 1.）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Arista の静的経路の既定は Cisco と同じ 1 で値は合っていたが、
  資料で確かめていなかったため、「同じ重みで複数あります」の出典が「未確認: …」になっていた。
- 直したこと: defaults.py の静的経路の出典に Arista の資料を足した（値は変えていない）。試験は `backend/tests/test_defaults.py`。
- 未確認のまま: Aruba CX の STP の既定のモード。AOS-CX の資料（arubanetworking.hpe.com・arubanetworks.com の techdocs、
  CLI ガイドの PDF）はこの環境から 403 で開けなかった。検索結果の要約は MSTP と言っているが、原文を見ていないので出典にしない。

## Junos の static defaults preference を読んでいなかった（2026-10-04 JST）

- 確かめた資料: Juniper「CLI Reference」の static (Routing Options) の defaults（Specify global static route options. These options
  only set default attributes inherited by all newly created static routes。route に書いた options は defaults より優先する）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `set routing-options static defaults preference 10` で既定を
  変えた機器でも、preference を書いていない経路を Junos の既定 5 と読んでいた。明示の 10 と並ぶ経路を「重みが違う」と見て、
  l3review「同じ重みで複数あります」を見逃していた。
- 直したこと: 読み手で defaults の preference を読み、経路にも転送先にも preference が無い経路の距離に当てる。試験は
  `backend/tests/test_l3review.py` の末尾。
- 残り: 論理システムの中の static は読んでいない（ルーティングインスタンスは次の項で読むようにした）。

## Huawei・H3C で既定の距離を書き換えた機器に、決まらない「同じ重み」を言っていた（2026-10-04 JST）

- 確かめた資料: H3C「13-Static Routing Commands」の ip route-static default-preference（If no preference is specified for a static
  route, the default preference applies. When the default preference is reconfigured, it applies to only newly added static
  routes.）、Huawei の Command Reference の同コマンド（the new default preference is valid for subsequent rather than existing
  IPv4 static routes）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `ip route-static default-preference 70` を書いた機器でも、
  preference を書いていない経路を既定の 60 と読み、明示の 60 と並べて「同じ重みで複数あります」と言っていた。その経路は
  書き換えの後に足していれば 70 で、重みは違う。
- 直したこと: 読み手で書き換えた値を `ParsedConfig.static_default_preference` に控える。l3review は、書き換えがある機器で
  preference を書いていない経路を含む組を判定しない（決めつけない）。試験は `backend/tests/test_l3review.py` の末尾。
- 未確認: 保存した Config の行の並びが、経路を足した順を保つか。保つと確かめられれば、書き換えの行より後の経路に新しい値を当てられる。

## Junos の囲みの形で、転送先を角括弧で並べた静的経路が丸ごと抜けていた（2026-10-04 JST）

- 確かめた資料: Juniper「ECMP Flow-Based Forwarding」の設定例（show routing-options: `route 172.16.1.0/24 next-hop
  [ 192.168.1.2 192.168.2.2 192.168.3.2 ];`）。囲みの形は入口（juniper.py の _flatten）で set 形式に直しているので、単純な
  経路と qualified-next-hop は前から読めていた。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 角括弧の並びは set 形式に直しても1行に残り、静的経路の読み手
  の形（転送先は1語）に合わずに経路ごと捨てていた。図にも経路の表にも出ず、点検も効かなかった。
- 直したこと: 静的経路の読み手の前で、角括弧の並びを転送先1つにつき1行に開く。試験は `backend/tests/test_l3review.py` の末尾。
- 残り: 論理システムの中の static は読んでいない（ルーティングインスタンスは次の項で読むようにした）。

## Junos のルーティングインスタンスの中の静的経路を読んでいなかった（2026-10-04 JST）

- 確かめた資料: Juniper「Routing, Interfaces, and NAT for User Logical Systems」の設定例（`set routing-instances pd-vr1
  routing-options static route 12.12.1.0/24 next-hop 10.0.1.1`）。
- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 静的経路の読み手は `set routing-options static …` で始まる
  全体の行だけを見ていて、`set routing-instances NAME routing-options static …` の経路が経路の表から丸ごと抜けていた。
  インスタンスの中の「同じ重みで複数あります」も効かなかった。
- 直したこと: インスタンスの行も読み、経路に `vrf=インスタンス名` を付けて全体の経路と分けて持つ。`static defaults
  preference` もインスタンスごとに持つ。角括弧の並びもインスタンスの行で開く。試験は `backend/tests/test_l3review.py` の末尾。
- 残り: 論理システム（`set logical-systems …`）の中の static は読んでいない。

## Junos の論理システムの中の静的経路（調べただけ。直していない）（2026-10-04 JST）

- 確かめた資料: Juniper「Understanding Logical Systems for Routers and Switches」（論理システムは自分の経路表・口・ポリシー・
  routing-instances を持つ）、「Routing, Interfaces, and NAT for User Logical Systems」の設定例。
- 見たこと: いまの読み手は `set logical-systems …` の行を読まない（経路も口も）。経路だけを読むと、口の帯を知らない
  「次の相手へ行けません」が誤った指摘を出すので、口と一緒に別の機器として読むまで手を付けない。判断は decisions.md。

## 既定の振る舞いに拠る4つの指摘に、資料の名前が出ていなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 値ではなく「書かなければこう動く」に拠る指摘
  （HSRP は既定で役を取り返さない、DHCP の見張りは VLAN に掛けるまで働かない、ARP の見張りの判断のもと、OSPF の
  passive の口は hello を出さない）は、出典がコードのコメントにしか無く、画面にも API にも出ていなかった。
- 直したこと: コメントにあった出典（Cisco の FHRP Command Reference・Nexus の HSRP 既定値表、Catalyst 3560 の DHCP Features、
  Catalyst 9300 の Dynamic ARP Inspection、IOS XE の Default Passive Interfaces）を defaults.py に置き、指摘に付けた。
  passive は資料で確かめた OSPF のときだけ付ける。試験は `backend/tests/test_l3review.py`・`test_configcheck.py` の末尾。

## Junos の論理システムの中を読んでいないことが、結果に出ていなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `set logical-systems …` の行は読み手が読まないのに、
  設定の点検の結果には何も出ず、論理システムの中の口・経路・誤りが「無い」ように見えていた。
- 直したこと: 論理システムの行があれば notes に「論理システムの中は読んでいない」と出す。囲みの形も同じ。
  試験は `backend/tests/test_configcheck.py` の末尾（論理システムが無いときに出ないことは、新しい分岐の見張り）。
- 残り: 論理システムを別の機器として読むこと（設計は decisions.md）。

## NEC QX の静的経路の距離を Cisco の 1 として見ていた（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: NEC QX は preference を書かない静的経路が 60 なのに、
  既定を Cisco の 1 として見ていた。`preference 60` を書いた経路と書いていない経路が並ぶと「重みが違う（待機の形）」と
  取り違え、「同じ重みで複数あります」を落としていた。NEC IX（既定 1）と Aruba CX の STP（既定 MSTP）は値は合っていたが、
  指摘に「未確認」や出典なしが出ていた。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」セクション4 2.1.2 ip route-static（デフォルトは 60）・
  2.1.3 ip route-static default-preference（デフォルト 60）。NEC「IX-R/IX-V コマンドリファレンス 1.3」IPv4 編の
  スタティックルート追加（DISTANCE のデフォルト値 1）。「AOS-CX 10.12 Layer-2 Bridging Guide」（8400）の
  spanning-tree mode（The no form … sets the spanning tree mode to the default mstp）。
  QX の PDF は support.nec.co.jp から curl で取り、pypdf で読んだ。AOS-CX は Exa で HTML 版の原文を読んだ（PDF は 403）。
- 直したこと: `backend/app/services/defaults.py` に QX の既定 60 と、QX・IX・AOS-CX の出典を足した。
  試験は `backend/tests/test_l3review.py`・`test_configcheck.py` の末尾。
- 残り: NEC IX2000/IX3000 の資料は開いていない（IX-R/IX-V の資料で 1）。

## AlaxalA の静的経路で、weight の値をディスタンスと、poll などの語を口の名前と読んでいた（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）:
  - AlaxalA の経路行は IOS の読み手にそのまま渡していた。そのため `weight 100` の 100 をディスタンスと読み、
    `poll`・`noresolve` を口の名前と読み、`vlan 10` の 10 をディスタンスと読んでいた。
  - ディスタンスの既定を Cisco の 1 として見ていた（AlaxalA は 2）。2 を書いた経路と書いていない経路が並ぶと
    「重みが違う」と取り違え、「同じ重みで複数あります」を落としていた。
- 確かめた資料: アラクサラ「AX3660S コンフィグレーションコマンドレファレンス Vol.2」Ver.12.2 の ip route
  （書式と、<distance> の初期値 2）。「コンフィグレーションガイド Vol.3」Ver.12.2 の表24-3（スタティック経路 2）と
  「9.1.2 経路選択基準」（weight は同じディスタンスの組の中で選ぶ順。マルチパスの最大（既定 6）までは両方使う）。
- 直したこと: `backend/app/services/configparse/alaxala.py` で、経路行の後ろの語を IOS の読み手に渡す前に整える。
  `backend/app/services/defaults.py` に既定 2 と出典を足した。試験は `test_l3review.py`・`test_configparse_alaxala.py` の末尾。
- 残り: 経路の行での口の書き方（`vlan 10`）は、コマンドレファレンスの「パラメータに指定できる値」を開いておらず未確認。
  NEC IX2000/IX3000 の資料そのものも開いていない（IX-R/IX-V の資料の ip route の項に、差分の記載は無い）。

## STP の既定のモードの出典が、AlaxalA・Dell OS10・Cisco Business で出ていなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「STP のモードを決めていません」の指摘に、
  この3機種では既定のモードの資料が付いていなかった（確かめていない機種には出さない作りのため）。
- 確かめた資料: アラクサラ「AX3660S コンフィグレーションガイド Vol.1」Ver.12.1 27.2.2（設定しない場合 pvst）、
  Dell「SmartFabric OS10 User Guide Release 10.6.0」Enable Rapid-PVST（By default, Rapid-PVST is enabled）、
  Cisco「Business Switches 350 Series CLI Guide」spanning-tree mode（The default is RSTP）。
  あわせて AlaxalA の経路の行での口の書き方を、コマンドレファレンスの「パラメータに指定できる値」表1-7
  （`vlan <vlan id>`・`null 0`）で確かめ、前の項の「未確認」を外した。
- 直したこと: `backend/app/services/defaults.py` の `_STP_MODE_SOURCES` に3機種を足した。試験は `test_configcheck.py` の末尾。
- 残り: AOS-S（aruba_os）の STP の既定は資料に当たっていない。Huawei・H3C は `stp mode` で書くため、この指摘の対象外。

## Huawei・H3C・NEC QX で STP のモードを決めていなくても、指摘が出ていなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「STP のモードを決めていません」は
  `spanning-tree` で始まる行だけを見ていた。`stp …` で書く3機種では、STP を使っていても黙って通っていた。
- 確かめた資料（3機種とも既定は MSTP）: Huawei「S9300 and S9300E V200R008 Configuration Guide - Ethernet Switching」
  Configuring Basic MSTP Functions、H3C「07-Spanning tree configuration」Setting the spanning tree mode、
  NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」8.1.31 stp mode。
- 直したこと: `backend/app/services/configcheck.py` の `_stp` で、3機種は `stp` / `stp mode` の行を見る。
  行頭の `stp disable`・`stp global disable`（装置全体で止めている）では出さない。出典は `defaults.py` に足した。
  試験は `test_configcheck.py` の末尾（モードを書いた機器・止めた機器に出ないことは新しい分岐の見張り）。
- 残り: AOS-S（aruba_os）は、HPE の Advanced Traffic Management Guide 16.10/16.11 で MSTP の説明は読めたが、
  「既定のモード」を言い切る文に当たっていないので未確認のまま。

## Huawei の全体の端末口指定と、口ごとの BPDU フィルタを読んでいなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）:
  - `stp edged-port default`（全部の口を端末口にする）を読んでおらず、BPDU protection が無くても
    「端末用の口に、BPDU guard が入っていません」が出ていなかった。
  - 口ごとの `stp bpdu-filter enable`（その口の STP を止めるのと同じ）は IOS の書き方しか見ておらず、黙って通っていた。
- 確かめた資料: Huawei「Fat AP and Cloud AP V200R019C00 Command Reference」stp edged-port default、
  「S220, S310, S530, and S620 V600R025C00 Configuration Guide - Ethernet Switching」Configuring Edge Ports and
  BPDU Filtering Ports（口の `stp edged-port disable` で外れる）、「NetEngine AR6700V-L V600R024C00 Command Reference」
  stp bpdu-filter（the port does not process or send BPDUs）。
- 直したこと: `backend/app/services/configcheck.py` の `_stp`。全体の端末口指定は Huawei だけ（資料が Huawei のもの）、
  対象は `port link-type` のある口で `stp edged-port disable` を書いていない口。全体の `stp bpdu-protection` があれば出さない。
  BPDU フィルタは `port link-type trunk` の口を trunk として HIGH にする。試験は `test_configcheck.py` の末尾。
- 残り: 全体の `stp bpdu-filter default` は見ていない。H3C・NEC QX の同等の書き方は資料に当たっていない。

## 口で外した BPDU 保護と、Huawei の全体の BPDU フィルタを読んでいなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）:
  - NEC QX・H3C で、全体の `stp bpdu-protection` があると、口で `stp port bpdu-protection disable` にした
    端末口まで守られていることにしていた（`bpdu-protection` の語だけを見ていたため、disable も「守り」と読んでいた）。
  - Huawei の全体の `stp bpdu-filter default`（`stp bpdu-filter disable` の口以外が BPDU フィルタ）を読んでいなかった。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」8.1.17 stp bpdu-protection・8.1.22 stp edged-port・
  8.1.35 stp port bpdu-protection（口の状態は既定で全体と同じ。disable で無効）、H3C「07-Spanning tree commands」の
  stp port bpdu-protection、Huawei「NetEngine AR6700V-L V600R024C00 Command Reference」の stp bpdu-filter。
  NEC QX のこの資料には BPDU フィルタのコマンドは無い。
- 直したこと: `backend/app/services/configcheck.py` の `_stp`。試験は `test_configcheck.py` の末尾。

## Huawei・H3C・NEC QX の DHCP の見張りの決め忘れを、指摘していなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: DHCP snooping の点検は IOS の `ip dhcp snooping` だけを見ていた。
  `dhcp snooping enable` で書く3機種では、信用する口が1つも無い（本物の DHCP サーバーの応答まで落とす）設定も黙って通っていた。
- 確かめた資料: Huawei「S5700 and S6700 V200R025C00 Command Reference」の dhcp snooping enable（全体で有効にしたうえで、
  VLAN・口・BD でも有効にする）と dhcp snooping trusted（既定で全部の口が信用しない口）、
  H3C「DHCP snooping configuration」（全体で有効にすると全部の口で有効。有効にすると全部の口が信用しない口）、
  NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」7.1.2 dhcp snooping enable・7.1.7 dhcp snooping trust。
- 直したこと: `backend/app/services/configcheck.py` に `_undo_dhcp_snooping` を足した。Huawei は VLAN・口で有効に
  していなければ「どの VLAN にも掛けていません」、3機種とも信用する口が無ければ HIGH。出典は `defaults.py`。
  試験は `test_configcheck.py` の末尾（信用する口があるときに出ないことは新しい分岐の見張り）。
- 残り: ARP 検査（Huawei の arp anti-attack check user-bind、H3C の arp detection）と port-security の undo 系の書き方は、
  まだ見ていない。

## Huawei・H3C・NEC QX の ARP の見張りで通信が落ちる形を、指摘していなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: ARP の見張りの点検は IOS の `ip arp inspection` だけを見ていた。
  H3C・NEC QX の `arp detection enable` で信用する口が無い設定、Huawei の `arp anti-attack check user-bind enable` で
  判断のもと（DHCP の見張り・静的な対応表）が無い設定は、黙って通っていた。
- 確かめた資料: H3C「20-ARP attack protection configuration」（照合できない ARP は捨てる。口の既定は untrusted）、
  NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」8.6.1・8.6.3、Huawei「S300, S500, S2700, S5700, and S6700
  V200R024C00 Configuration Guide - Security」Configuring DAI（対応表に無い ARP は捨てる）。
- 直したこと: `backend/app/services/configcheck.py` に `_undo_arp_inspection` を足した。出典は `defaults.py`。
  試験は `test_configcheck.py` の末尾（信用する口・静的な対応表があるときに出ないことは新しい分岐の見張り）。
- 見なかったこと: H3C・NEC QX の「判断のもとが無い」形。H3C の資料は版によって「規則が無ければ全部通す」
  （IPS 向けの版）と「どれも無ければ通らない」（2025 年の版）で書き方が違うため、決めつけない。

## Huawei の port-security で「黙って捨てる」指定を、指摘していなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「ポートセキュリティが、黙って落とす設定になっています」は
  IOS の `violation protect` だけを見ていた。Huawei の `port-security protect-action protect`（超えた分を記録なしで捨てる）は通っていた。
- 確かめた資料: Huawei「S1720, S2700, S5700, and S6720 V200R011C10 Command Reference」の port-security protect-action
  （既定は restrict。protect は捨てるだけ、restrict は捨てて trap を出す）と port-security max-mac-num（既定 1）。
- 直したこと: `backend/app/services/configcheck.py` の該当の指摘で、Huawei の書き方も見る。証跡は書いてある行そのものにした。
  試験は `test_configcheck.py` の末尾（既定のまま＝restrict のときに出ないことは新しい分岐の見張り）。
- 見なかったこと: Huawei の電話用の口（voice-vlan）で覚える数が足りない形。電話の MAC を port-security の数に入れるかを
  資料で確かめていない。H3C・NEC QX の port-security（port-mode で動きが決まる）は資料に当たっていない。

## H3C の SNMP の既定の合言葉を、simple を書いた形で見落としていた（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「SNMP の合言葉が既定のままです」は
  `snmp-agent community read public` の形だけを見ていた。H3C の `snmp-agent community read simple public`
  （名前の前に simple）と、RBAC の形 `snmp-agent community simple public user-role …` は通っていた。
- 確かめた資料: H3C「02-SNMP commands」の snmp-agent community（VACM: `{ read | write } [ simple | cipher ] community-name`、
  RBAC: `[ simple | cipher ] community-name user-role role-name`。simple で入れても保存は暗号化）。
  保存済みの設定には simple の形は残らないが、下書き（作る設定）には出る。
- 直したこと: `backend/app/services/configcheck.py` の該当の正規表現。cipher の後ろ（暗号化した名前）は見ない。
  試験は `test_configcheck.py` の末尾（cipher の形で出ないことは見張り）。
- 見なかったこと: H3C・NEC QX・Huawei の利用者のパスワードの保存形式。H3C Comware 7 は simple で入れても hash して保存する
  （「Fundamentals Command Reference」set authentication password）ので、保存済みの設定に平文は残らない。
  Huawei の local-user の書き方（simple の有無）は資料に当たっていない。

## NEC QX の時間切れなし・SSH1 互換の書き方を、指摘していなかった（2026-10-04 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「入ったままの画面が、いつまでも閉じません」は IOS の
  `exec-timeout 0 0`、「SSH が古い方式のままです」は `ip ssh version 1` だけを見ていた。NEC QX の `idle-timeout 0`（0 0）と
  `ssh server compatible-ssh1x enable` は通っていた。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.16 idle-timeout（既定 10 分。0 にするとタイムアウトしない）、
  6.1.8 ssh server compatible-ssh1x enable（既定は無効。SSH1 クライアントをサポートする）。
- 直したこと: `backend/app/services/configcheck.py` の該当の正規表現。`idle-timeout 0 30`（30 秒）は言わない。
  試験は `test_configcheck.py` の末尾（`0 30` で出ないことは見張り）。
- 残り: Huawei・H3C は同じ書き方だが、この周では資料に当たっていない（正規表現は機種を問わず同じ行に当たる）。

## NEC QX・Huawei の管理用 web（http）を開く行を、指摘していなかった（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「管理用の web が開いています」は IOS・Junos・FortiGate の
  書き方だけを見ていた。NEC QX の `ip http enable`、Huawei の `http server enable`・`http ipv6 server enable` は通っていた。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.18 ip http enable（既定は無効）・2.1.22 ip https enable、
  Huawei「S1720, S2700, S5700, and S6720 V200R011C10 Command Reference」の http server enable / http secure-server enable、
  「S5700 and S6700 V200R024C10 Web-based Configuration Guide」（IPv4 の HTTP は既定で有効）。
- 直したこと: `backend/app/services/configcheck.py` の該当の正規表現。https（ip https enable / http secure-server enable）は言わない。
  試験は `test_configcheck.py` の末尾（https で出ないことは見張り）。
- 見なかったこと: Huawei で行が無いのに既定で開いている形。既定は版で違うので、行が無い機器を「開いている」とも
  「閉じている」とも言わない。H3C の ip http enable の既定は資料に当たっていない（行があれば同じ正規表現に当たる）。

## NEC QX の入る口で認証をなくす `authentication-mode none` を、指摘していなかった（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `line vty 0 63` の下に `authentication-mode none` があっても、
  何も言っていなかった。その口からは合言葉なしで入れる。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.1 authentication-mode
  （`authentication-mode { none | password | scheme }`、User line view、既定は VTY が password・AUX line が none）。
- 直したこと: `backend/app/services/configcheck.py` の `_login_without_authentication`。`line …` / `user-interface …` の
  まとまりの中の none だけを言う（Huawei の `aaa` の下の authentication-scheme にも同じ字面の行があるため）。
  試験は `test_configcheck.py` の末尾（password・scheme・aaa の下の none で出ないことは見張り）。
- 見なかったこと: 書いていない AUX（既定で none）。Huawei の user-interface の none は資料で確かめていない（行があれば同じく言う）。

## FTP サーバーを開く `ftp server enable` を、指摘していなかった（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: telnet や暗号化しない web は言っていたのに、
  同じく中身が丸見えの FTP サーバーを開く行は何も言っていなかった。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」4.1.8 ftp server enable（既定は無効）・
  6.1.3 sftp server enable。
- 直したこと: `backend/app/services/configcheck.py` の行の規則に「FTP サーバーが開いています」（HIGH）を足した。
  行頭から見るので `sftp server enable` には当たらない。試験は `test_configcheck.py` の末尾（sftp と undo の形は見張り）。
- 見なかったこと: Huawei・H3C も同じ字面で当たるが、その資料には当たっていない（未確認）。Cisco などほかの書き方は足していない。

## Cisco の `ip directed-broadcast` を、指摘していなかった（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 口に `ip directed-broadcast` があっても、何も言っていなかった。
  外から網あての一斉送信を送り込まれ、増幅攻撃の踏み台になる。
- 確かめた資料: Cisco「Cisco IOS IP Application Services Command Reference」の ip directed-broadcast
  （既定は無効、ACL を付けると許したものだけを流す）、「IP Addressing Configuration Guide, Cisco IOS XE 17.x」
  （12.0 から既定で無効）。
- 直したこと: `backend/app/services/configcheck.py` の行の規則に MEDIUM で足した。ACL を付けた形と no の形は言わない。
  試験は `test_configcheck.py` の末尾（ACL 付き・no の形は見張り）。
- 見なかったこと: ほかのメーカーの同じ働きの書き方（Huawei・H3C・NEC QX などは資料に当たっていない）。

## NEC QX の Config に、Cisco の書き方の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 行の規則の why は Cisco の書き方で直し方を書いている。
  QX の `telnet server enable` に「transport input ssh」、`ip http enable` に「no ip http server」を案内していて、
  QX ではそのまま打てなかった。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」2.1.41 telnet server enable・2.1.29 protocol inbound・
  2.1.18 ip http enable・4.1.14 snmp-agent community。
- 直したこと: `backend/app/services/configcheck.py` の `_QX_FIXES`。機種が NEC QX のときだけ、題で引いて why を QX の書き方の文に替える。
  試験は `test_configcheck.py` の末尾（Cisco の Config では今までどおりの文が出ることは見張り）。
- 見なかったこと: Huawei・H3C は似た書き方だが資料で確かめていないので替えていない（未確認）。
  QX の `password simple` は、マニュアル 1.2.8 では保存するとハッシュ化される。Config の文に平文があることは変わらないので、指摘はそのまま。

## NEC QX の SSH1 互換とパスワードの指摘に、Cisco の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: QX の `ssh server compatible-ssh1x enable` に「version 2 にしてください」、
  `password simple` に「username ... secret を使うと」を案内していて、QX では打てなかった。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」6.1.8 ssh server compatible-ssh1x enable
  （無効にする形は `undo ssh server compatible-ssh1x` で、enable は付かない）・7 章 1.2.8 password（装置管理ユーザは hash か simple。
  simple でも保存するとハッシュになる）。
- 直したこと: `backend/app/services/configcheck.py` の `_QX_FIXES` に2件を足した。パスワードの文は「機器の中ではハッシュになるが、
  Config の文では読める」と、QX の振る舞いに合わせて書いた。試験は `test_configcheck.py` の `test_qx_findings_show_the_qx_way_to_fix`。
- 見なかったこと: Huawei・H3C（資料で確かめていない）。

## Junos の Config に、Cisco の書き方の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `set system services telnet` に「transport input ssh」、
  `web-management http` に「no ip http server」、`authorization read-write` に「RO で足ります」を案内していた。
- 確かめた資料: Juniper「Day One: Juniper Ambassadors' Cookbook for Enterprise」の初期設定の例（`delete system services telnet`・
  `delete system services web-management`）、Juniper「Junos OS CLI Reference」の community (SNMP)・authorization (SNMP)。
- 直したこと: `backend/app/services/configcheck.py` に `_JUNOS_FIXES` を足し、機種ごとの直し方を `_FIXES_BY_VENDOR` で引く形にした
  （QX の `_QX_FIXES` も同じ表に載せた）。試験は `test_configcheck.py` の `test_junos_findings_show_the_junos_way_to_fix`。
- 見なかったこと: 囲みの形の Junos の Config（行の規則がもともと set 形式しか見ていない）。FortiGate・ヤマハなどほかの機種の直し方。

## FortiGate の Config に、Cisco の書き方の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `set allowaccess … telnet` に「transport input ssh」、
  `set allowaccess … http` に「no ip http server」を案内していて、FortiOS ではそのまま打てなかった。
- 確かめた資料: Fortinet Community「Technical Tip: Useful multi-option values CLI commands for existing lists」（unselect は
  一覧から項目だけを外す）、「Technical Tip: How to meet the requirements for FSBP SH01.1 and SH01.2」（`unselect allowaccess http telnet`）。
- 直したこと: `backend/app/services/configcheck.py` に `_FORTIGATE_FIXES` を足し、`_FIXES_BY_VENDOR` に載せた。
  試験は `test_configcheck.py` の `test_fortigate_findings_show_the_fortigate_way_to_fix`。
- 見なかったこと: ヤマハ（telnetd service）・アライド（service telnet）の直し方。資料に当たってから足す。

## ヤマハ・アライドの telnet の指摘に、Cisco の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: ヤマハの `telnetd service on`、アライドの `service telnet` に
  「transport input ssh」を案内していて、どちらの機器でも打てなかった。
- 確かめた資料: ヤマハ「RTX コマンドリファレンス」4 章「TELNET サーバー機能の ON/OFF の設定」（telnetd service on|off、初期値は on）、
  Allied Telesis「AlliedWare Plus Best Practice Guide」の Telnet の節（`no service telnet`）・「Getting Started with AlliedWare Plus」の既定値の表。
- 直したこと: `backend/app/services/configcheck.py` に `_YAMAHA_FIXES`・`_ALLIED_FIXES` を足し、`_FIXES_BY_VENDOR` に載せた。
  試験は `test_configcheck.py` の `test_yamaha_and_allied_telnet_show_their_way_to_fix`。
- 見なかったこと: ヤマハの SSH を有効にする行の書き方（資料に当たっていないので、直し方の文には書いていない）。

## H3C の telnet の指摘に、Cisco の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: H3C の `telnet server enable` に「transport input ssh」を案内していた。
- 確かめた資料: H3C「Fundamentals Command Reference」03-Login management commands の telnet server enable（undo で止める、既定は無効）と
  protocol inbound { all | ssh | telnet }。QX と同じ書き方なので、QX の文をそのまま使う。
- 直したこと: `backend/app/services/configcheck.py` に `_H3C_FIXES` を足し、`_FIXES_BY_VENDOR` に載せた。
  試験は `test_configcheck.py` の `test_h3c_telnet_shows_the_h3c_way_to_fix`。
- 見なかったこと: H3C の http・SNMP・SSH1・パスワードの直し方（この資料には無いので、資料に当たってから足す）。Huawei はまだ。

## H3C の http・SNMP の書き込みの指摘に、Cisco の直し方を案内していた（2026-10-05 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: H3C の `ip http enable` に「no ip http server」、
  `snmp-agent community write` に「RO で足ります」を案内していた。
- 確かめた資料: H3C「Fundamentals Command Reference」03-Login management commands の ip http enable / ip https enable
  （既定は無効。http を有効にすると https も自動で有効になる）、H3C「02-SNMP commands」の snmp-agent community { read | write }。
- 直したこと: `backend/app/services/configcheck.py` の `_H3C_FIXES` に2件を足した（QX と同じ書き方なので QX の文を使う）。
  試験は `test_configcheck.py` の `test_h3c_http_and_snmp_show_the_h3c_way_to_fix`。
- 見なかったこと: H3C の SSH1 とパスワードの直し方（資料に当たっていない）。RBAC の形の community（user-role で権限を決める）。

## H3C の SSH1 互換の指摘に、Cisco の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: H3C の `ssh server compatible-ssh1x enable` に「version 2 にしてください」を案内していた。
- 確かめた資料: H3C「SSH commands」の ssh server compatible-ssh1x enable（`undo ssh server compatible-ssh1x [ enable ]` で既定に戻す、
  既定は SSH1 のクライアントを受け入れない）。
- 直したこと: `backend/app/services/configcheck.py` の `_H3C_FIXES` に足した（QX と同じ書き方なので QX の文を使う）。
  試験は `test_configcheck.py` の `test_h3c_http_and_snmp_show_the_h3c_way_to_fix`。
- 見なかったこと: H3C のパスワードの直し方（資料に当たっていない）。

## Huawei の telnet の指摘に、Cisco の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Huawei の `telnet server enable`・`protocol inbound telnet` に
  「transport input ssh」を案内していた。
- 確かめた資料: Huawei「S Series Campus Switches Troubleshooting Guide (V100 and V200)」の SSH/Telnet Attack（undo telnet server enable）と
  FAQs About Basic Configuration（既定は V200R005 以降で無効、V200R003 以前で有効）、「Fat AP and Cloud AP V200R010C00 CLI-based
  Configuration Guide」の Logging In to the Device（protocol inbound ssh は authentication-mode aaa が要る）。
- 直したこと: `backend/app/services/configcheck.py` に `_HUAWEI_FIXES` を足した。vty は user-interface で書くので QX の文は使わない。
  試験は `test_configcheck.py` の `test_huawei_telnet_shows_the_vrp_way_to_fix`。
- 見なかったこと: Huawei の http・SNMP・パスワードの直し方（資料に当たってから足す）。

## Huawei の管理用 web の指摘に、Cisco の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Huawei の `http server enable` に「no ip http server」を案内していた。
- 確かめた資料: Huawei「S Series Campus Switches Troubleshooting Guide (V100 and V200)」の TCP/UDP Attack
  （undo http server enable / undo http secure-server enable）と FAQs About Basic Configuration（http server enable は既定で有効）。
- 直したこと: `backend/app/services/configcheck.py` の `_HUAWEI_FIXES` に足した。試験は `test_huawei_http_shows_the_vrp_way_to_fix`。
- 見なかったこと: `http ipv6 server enable` を止める形（undo の形を資料で確かめていないので、直し方の文には書いていない）。Huawei の SNMP・パスワード。

## Huawei の SNMP の書き込みの指摘に、Cisco の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Huawei の `snmp-agent community write` に「RO で足ります」を案内していた。
- 確かめた資料: Huawei「CloudEngine S3700, S5700, and S6700 V600R023C00 Configuration Guide」の Configuring Basic SNMPv1 Functions
  （snmp-agent community { read | write }）。書き方は QX・H3C と同じなので、QX の文を使う。
- 直したこと: `backend/app/services/configcheck.py` の `_HUAWEI_FIXES` に足した。試験は `test_huawei_snmp_write_shows_the_vrp_way_to_fix`。
- 見なかったこと: Huawei のパスワード・SSH の直し方。

## H3C の `password simple` の指摘に、Cisco の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: H3C の `password simple` に「username ... secret を使うと」を案内していた。
- 確かめた資料: H3C「AAA commands」の password（`password [ { hash | simple } string ]`。simple でも保存するとハッシュになる）。
  QX と同じ書き方・振る舞いなので、QX の文を使う。
- 直したこと: `backend/app/services/configcheck.py` の `_H3C_FIXES` に足した。試験は `test_h3c_plain_password_shows_the_h3c_way_to_fix`。
- 見なかったこと: ネットワークアクセスユーザ（class network）の password（cipher | simple）。

## Huawei の SSH1 互換の指摘に、Cisco の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Huawei の `ssh server compatible-ssh1x enable` に「version 2 にしてください」を案内していた。
- 確かめた資料: Huawei「S5700 and S6700 V200R025C00 Command Reference」の ssh server compatible-ssh1x enable
  （止める形は `undo ssh server compatible-ssh1x enable`。QX・H3C と違って enable が付く）、
  「S Series Campus Switches Troubleshooting Guide」の FAQs（V200R006 以前は既定で有効、V200R007 以降は無効）。
- 直したこと: `backend/app/services/configcheck.py` の `_HUAWEI_FIXES` に足した。試験は `test_huawei_ssh1_shows_the_vrp_way_to_fix`。
- 見なかったこと: Huawei のパスワードの書き方（`password simple` の規則に当たる書き方を VRP で確かめていない）。

## Cisco ASA の telnet の指摘に、IOS の直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: ASA の `telnet 10.0.0.0 255.255.255.0 inside` に
  「transport input ssh」を案内していた。ASA には IOS の line vty が無く、そのまま打てない。
- 確かめた資料: Cisco「Cisco Secure Firewall ASA Series Command Reference, T - Z Commands」の telnet
  （`no telnet` で1行ずつ消す、`clear configure telnet` で全部消す）、Cisco の技術文書 118075（`ssh ip_address mask interface_name`）。
- 直したこと: `backend/app/services/configcheck.py` に `_ASA_FIXES` を足し、`_FIXES_BY_VENDOR` に載せた。
  試験は `test_asa_telnet_shows_the_asa_way_to_fix`。
- 見なかったこと: ASA の http server（ASDM）の書き方。行の規則が ASA の `http` の行を見ていないので、別の件として扱う。

## ヤマハの telnet の指摘で、SSH を使えるようにする手順が抜けていた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: ヤマハの `telnetd service on` に「SSH を使ってください」とだけ案内していた。
  ヤマハの sshd は既定で止まっていて、ホスト鍵を作るまでつながらないので、その一言だけでは SSH に切り替えられない。
- 確かめた資料: ヤマハ「TELNET複数セッション機能、SSHサーバー機能」（`sshd service on`、`sshd host key generate`、
  `login user` で名前ありの利用者を作る）。
- 直したこと: `backend/app/services/configcheck.py` の `_YAMAHA_FIXES` の文に、3つの行を足した。
  試験は `test_yamaha_and_allied_telnet_show_their_way_to_fix` に1件足した。
- 足さなかったこと: ASA の `http server enable` は ASDM 用で、通信は HTTPS になる。暗号化しない Web として
  指摘すると誤検知になるので、規則には入れていない。

## Huawei の、機器に入る利用者の合言葉が元に戻せる形でも言っていなかった（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: パスワードの規則は Cisco の `username ... password` と
  QX の `password simple` しか見ておらず、Huawei の `local-user 名前 password cipher` を素通りしていた。
- 確かめた資料: Huawei「S5700 and S6700 V200R024C10 Configuration Guide - User Access and Authentication」の
  Configuring a Local User（`local-user user-name password { cipher | irreversible-cipher } password`。
  管理の口を許すと元に戻せない形へ自動で替わる）、「S300, S500, S2700, S5700, and S6700 V200R024C00 Command Reference」の
  Password setting（3DES・AES は元に戻せる。管理者の合言葉には元に戻せない方式を使う）。
- 直したこと: `backend/app/services/configcheck.py` に `_huawei_reversible_admin_password` を足した。
  service-type に telnet・ssh・terminal がある利用者だけを見る。試験は `test_huawei_reversible_admin_password_is_found`。
- 言わないこと: 8021x・ppp・web だけの利用者。認証の方式によっては元に戻せる形が要る。
- 見なかったこと: どの service-type を管理の種類とするかの一覧（ftp・http・api）。公式の一覧を確かめていないので入れていない。

## Huawei の合言葉の指摘が、http・ftp・api だけの管理者を見ていなかった（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 前の件で見る種類を telnet・ssh・terminal に絞っていたので、
  web から管理する利用者（service-type http）の `password cipher` を素通りしていた。
- 確かめた資料: Huawei「S7700 V200R023C10 Configuration Guide - User Access and Authentication」の Configuring a Local User
  （Administrative: api, ftp, http, ssh, telnet, x25-pad, and terminal / Common: 8021x, bind, ppp and web）。
- 直したこと: `backend/app/services/configcheck.py` の `_HUAWEI_LOGIN_SERVICES` を公式の管理の種類7つにした。
  試験は `test_huawei_reversible_password_for_web_or_ftp_admin_is_found`。

## Huawei の `http ipv6 server enable` に、IPv4 の口しか閉じない直し方を案内していた（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `http ipv6 server enable` の行も指摘していたが、
  案内は `undo http server enable` だけだった。これでは IPv6 の口が開いたまま残る。
- 確かめた資料: Huawei「智简园区网络解决方案 V100R020C10」の LAN网络站点配置（`http ipv6 server enable` /
  `undo http ipv6 server enable`、`http [ ipv6 ] secure-server enable`）、「S1720, S2700, S5700, and S6720 V200R011C10
  Command Reference」の display http server（IPv4 と IPv6 の口は別々に開け閉めする）。
- 直したこと: `backend/app/services/configcheck.py` の `_HUAWEI_FIXES` の文に IPv6 の閉じ方と https の形を足した。
  試験は `test_huawei_http_ipv6_shows_the_ipv6_way_to_close`。

## Huawei の `telnet ipv6 server enable` を、telnet の入口として見ていなかった（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 行の規則が `telnet server enable` しか見ておらず、
  IPv6 で telnet を開く `telnet ipv6 server enable` を素通りしていた。直し方も IPv4 の閉じ方だけだった。
- 確かめた資料: Huawei「WLAN AC V200R019C00 Command Reference」の telnet server enable
  （`telnet [ ipv6 ] server enable` / `undo telnet [ ipv6 ] server enable`、既定は止まっている）。
- 直したこと: `backend/app/services/configcheck.py` の telnet の行の規則に IPv6 の形を足し、`_HUAWEI_FIXES` の文に
  `undo telnet ipv6 server enable` を足した。試験は `test_huawei_telnet_ipv6_is_found_with_the_ipv6_way_to_close`。
- 見なかったこと: QX・H3C に IPv6 の telnet を開く別の書き方があるか。

## Huawei の `ftp ipv6 server enable` を、FTP の入口として見ていなかった（2026-10-06 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: FTP の行の規則が `ftp server enable` しか見ておらず、
  IPv6 で FTP を開く `ftp ipv6 server enable` を素通りしていた。
- 確かめた資料: Huawei「CloudEngine S3700, S5700 and S6700 V600R024C10 Command Reference」の ftp server enable
  （`undo ftp server [ enable ]`）と ftp ipv6 server enable（`undo ftp ipv6 server [ enable ]`）、
  「V600R024C10 Configuration Guide - Basic Configuration」の display ftp server（IPv4 と IPv6 の状態が別に出る）。
- 直したこと: `backend/app/services/configcheck.py` の FTP の行の規則に IPv6 の形を足し、`_HUAWEI_FIXES` に
  IPv6 の閉じ方を含む直し方を足した。試験は `test_huawei_ftp_ipv6_is_found_with_the_ipv6_way_to_close`。
- 見なかったこと: H3C の FTP の書き方（コメントに未確認と残した）。

## TFTP サーバーを開いた行を、何も言わずに通していた（2026-10-07 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: FTP は指摘していたが、合言葉の無い TFTP の
  `tftp server enable` は規則が無く素通りしていた。届く人なら誰でも機器のファイルを読み書きできる。
- 確かめた資料: H3C「06-FTP and TFTP commands」の tftp server enable（`undo tftp server enable`、既定は無効）、
  Huawei「NetEngine AR1000V V300R024 CLI-based Configuration Guide - Basic Configuration」の
  Managing Files When the Device Functions as a TFTP Server（使い終わったら `undo tftp server enable` で止める）。
- 直したこと: `backend/app/services/configcheck.py` の行の規則に「TFTP サーバーが開いています」を足した。
  試験は `test_tftp_server_enable_is_found`（H3C と Huawei の2件）。
- ついでに確かめたこと: H3C の FTP は `undo ftp server enable` で止める（同じ資料）。今の案内のままで合っていたので、
  FTP の規則のコメントの「未確認」を外しただけで、動きは変えていない。
- 見なかったこと: Cisco IOS の `tftp-server`。書き方が違い、資料に当たっていない。

## Cisco IOS の `tftp-server` を、相手を絞らずに書いていても言っていなかった（2026-10-07 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 周10 で H3C・Huawei の TFTP を足したが、
  Cisco IOS の `tftp-server` は書き方が違うので素通りしていた。アクセスリストの番号が無いと、届く人は誰でもファイルを取り出せる。
- 確かめた資料: Cisco「Cisco IOS Configuration Fundamentals Command Reference」の tftp-server
  （`tftp-server flash [partition-number:] filename1 [alias filename2] [access-list-number]`、`no tftp-server` で消す）、
  「System Management Configuration Guide, Cisco IOS XE 17.x」の Configuring Basic File Transfer Services。
- 直したこと: `backend/app/services/configcheck.py` に `_ios_open_tftp_server` を足した。末尾が番号の行は言わない。
  試験は `test_ios_tftp_server_without_access_list_is_found`。
- 決めたこと: 重さは中にした。IOS の tftp-server は書いたファイルを読ませるだけで、H3C・Huawei のような書き込みは無いため。

## Huawei の閉じ方で、開いていない側（IPv4 / IPv6）の行まで案内していた（2026-10-07 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 直し方の文を題ごとに1つしか持てなかったので、
  `telnet server enable` だけの Config にも `undo telnet ipv6 server enable` を並べていた（http・ftp も同じ）。
  読み手は「IPv6 も開いているのか」と迷う。
- 確かめた資料: 周7〜周9 と同じ（IPv4 と IPv6 の口は別々の行で開け閉めする）。
- 直したこと: `backend/app/services/configcheck.py` の直し方の表で、文の代わりに「当たった行から文を作る関数」も
  持てるようにした（`Fix`、`_by_family`）。Huawei の telnet・http・ftp は、当たった行に応じて開いている側の閉じ方だけを出す。
  vty の行だけで当たったときは、今までどおり IPv4 の閉じ方を出す。
  試験は `test_huawei_fix_names_only_the_address_family_that_is_open`（6件）と
  `test_huawei_fix_names_both_families_when_both_are_open`。

## AOS-S で STP のモードを決めていないとき、既定のモードの出典が出ていなかった（2026-10-10 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「STP のモードを決めていません」の指摘に、
  AOS-S（aruba_os）だけ既定のモードの資料が付いていなかった。10/4 の時点では「既定のモード」を言い切る文に当たれず、未確認にしていた。
- 確かめた資料: HPE「Aruba 2930F / 2930M Advanced Traffic Management Guide for AOS-S 16.11」の
  Setting spanning tree to operate in 802.1D legacy mode（`spanning-tree legacy-mode` の Default: MSTP-operation。
  no の形で既定の 802.1s native mode（MSTP）に戻る）。モードを選ぶ節（Selecting MSTP as the spanning tree mode）には
  既定の書き込みが無く、こちらの節に書いてあった。
- 直したこと: `backend/app/services/defaults.py` の `_STP_MODE_SOURCES` に aruba_os を足した。
  試験は `test_a_missing_stp_mode_names_the_vendor_default` に AOS-S の1件。確かめていない機種の見張りは fortinet に替えた。
- 見なかったこと: 2930F/M 以外の AOS-S 機種（3810・5400R など）の版ごとの違い。同じ Guide の別冊に当たっていない。

## Huawei の user-interface の `authentication-mode none` に、QX の直し方（scheme）を案内していた（2026-10-10 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 「機器に入る口で、認証を求めていません」の直し方が
  機種を問わず「password か scheme」で、VRP には scheme が無い。Huawei の none そのものも資料で確かめていなかった。
- 確かめた資料: Huawei「S300, S500, S2700, S5700, and S6700 V200R023C00 Command Reference」の UI Configuration Commands
  「authentication-mode (user interface view)」（`authentication-mode { aaa | password }`。none は空パスワードの追加機能を
  入れたときに書け、危ないので aaa か password を勧める。password を入れると「安全でない、aaa を強く勧める」と出る。
  コンソールの既定は V200R009 以前が認証なし、V200R010〜R019 が aaa、V200R020 以降が password）。
- 直したこと: `backend/app/services/configcheck.py` の `_login_without_authentication` に機種を渡し、Huawei には
  `authentication-mode aaa` と local-user を案内する。試験は `test_huawei_login_without_authentication_shows_the_vrp_way_to_fix`。
- 見なかったこと: 書いていないコンソール（V200R009 以前は既定で認証なし）。版が Config から読めないことが多いので言わない。

## Junos の論理システムを読む部品（分けるだけ。取り込みにはつないでいない）（2026-10-10 JST）

- 経緯: 10/4 に「論理システムは口と一緒に別の機器として読む」と決め（decisions.md）、設計だけ書いて未着手だった。
- 確かめた資料: Juniper「Routing, Interfaces, and NAT for User Logical Systems」の set の設定例
  （`set logical-systems LS interfaces …`・`set logical-systems LS routing-options static …`）、
  「Understanding Logical Systems for Routers and Switches」（論理システムは自分の口・経路表・ポリシーを持つ）。
- 作ったもの: `backend/app/services/configparse/juniper.py` の `split_logical_systems`。平らにした set 行を、全体と
  論理システムごとに分け、`logical-systems NAME` を外して `parse` にそのまま渡せる形にする。囲みの形も扱う。
  試験は `test_configparse_juniper.py` の末尾3件（直す前は関数が無く落ちる）。
- まだやっていないこと: 1ファイルから複数の機器を返す入口（`parse_all`）と、取り込み（pipeline・configcheck）への接続。
  取り込みと台帳の作りに関わるので、別の周で設計を確かめてから進める。notes の「論理システムの中は読んでいない」はそのまま。

## ヤマハの `sshd service on` が、機能の一覧に SSH として出ていなかった（2026-10-10 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 機能の一覧（`configparse/features.py`）は、ヤマハの sshd を
  「未確認」として拾っていなかった。SSH で管理しているヤマハの機器でも、一覧に SSH が出なかった。
- 確かめた資料: ヤマハ「TELNET複数セッション機能、SSHサーバー機能」（`sshd service on` で SSH サーバーを動かす。既定は off）。
  10/6 の周4 で、telnet の直し方を書くときに同じ資料で確かめていた。
- 直したこと: `backend/app/services/configparse/features.py` の SSH の規則に `sshd service on` を足し、冒頭の「未確認」から外した。
  `sshd service off` は拾わない。試験は `test_features.py` の `test_yamaha_sshd_is_found_as_ssh`。

## H3C の `ssh server enable` が、機能の一覧に SSH として出ていなかった（2026-10-11 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 機能の一覧（`configparse/features.py`）は、H3C の ssh server を
  「未確認」として拾っていなかった。SSH で管理している H3C の機器でも、一覧に SSH が出なかった。
- 確かめた資料: H3C「13-SSH commands」の ssh server enable（Stelnet サーバーを動かす。既定は無効。`undo ssh server enable` で止める）。
- 直したこと: `backend/app/services/configparse/features.py` の SSH の規則に `ssh server enable` を足し、冒頭の「未確認」から外した。
  行頭から見るので `undo ssh server enable` は拾わない。試験は `test_features.py` の `test_h3c_ssh_server_enable_is_found_as_ssh`。
- 見なかったこと: 同じ字面を使う他の機種（NEC QX など）。規則は機種を問わず当たるが、そちらの資料はこの周では当たっていない。

## H3C の `dhcp select server` が、機能の一覧に DHCP サーバーとして出ていなかった（2026-10-11 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: DHCP サーバーの規則は Huawei の `dhcp select global|interface` しか
  見ておらず、H3C の口の下の `dhcp select server` を「未確認」として拾っていなかった。
- 確かめた資料: H3C「03-DHCP configuration」Enabling the DHCP server on an interface（Comware 7 の `dhcp select server`）、
  「03-DHCP Commands」の `dhcp select server global-pool [ subaddress ]`（Comware 5 の形）。
- 直したこと: `backend/app/services/configparse/features.py` の DHCP サーバーの規則に2つの形を足し、冒頭の「未確認」から外した。
  試験は `test_features.py` の `test_h3c_dhcp_select_server_is_found_as_dhcp_server`。
- 見なかったこと: Comware 7 は口の DHCP サーバーが既定で有効なので、行が無くても配っている場合がある（全体の `dhcp enable` と
  アドレスの塊で決まる）。行がある場合だけ拾う今の形では、その機器を取りこぼす。

## NEC QX の `port trunk permit vlan` の読み方を、マニュアルで確かめた（直していない）（2026-10-11 JST）

- 経緯: `configparse/vlanlist.py` に「NEC QX の `port trunk permit vlan` は未確認」と残っていた。
- 確かめた資料: NEC「QX-S1100G シリーズ コマンドマニュアル 1.9版」10.2.7 port trunk permit vlan
  （`port trunk permit vlan { vlan-id-list | all }`。範囲は vlan-id1 to vlan-id2、1〜4094。繰り返し実行すると各行の VLAN が通る。
  undo で外す。既定は VLAN 1 だけ。例 `port trunk permit vlan 2 4 50 to 100`）。
- 分かったこと: 今の読み手（`nec.py`）は、範囲・行の足し合わせ・undo のどれもマニュアルどおりに読んでいた。コードは直していない。
- したこと: マニュアルの作例と、行を分けて足す・undo で外す形を試験で押さえた（`test_configparse_nec.py` の
  `test_qx_trunk_permit_vlan_reads_the_manual_example_and_adds_up_lines`。今の読み方を守る見張り）。「未確認」を外した。

## Junos の MSTP・VSTP が、機能の一覧に STP として出ていなかった（2026-10-11 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 機能の一覧（`configparse/features.py`）は Junos の `set protocols rstp` だけを
  STP として拾い、`mstp`・`vstp` は「未確認」として拾っていなかった。MSTP・VSTP で組んだ Junos の機器では、一覧に STP が出なかった。
- 確かめた資料: Juniper「Example: Configuring Network Regions for VLANs with MSTP on EX-series Switches」
  （`set protocols mstp configuration-name region1`・`set protocols mstp msti 1 vlan [10 20]` など）、
  「Configuring VSTP」（`set protocols vstp vlan vlan-id`。VSTP と RSTP は同時に設定できる）。
- 直したこと: `backend/app/services/configparse/features.py` の Junos の STP の規則を rstp・mstp・vstp にし、モードとして出す。
  冒頭の「未確認」から外した。試験は `test_features.py` の `test_junos_mstp_and_vstp_are_found_as_stp_with_their_mode`。

## Junos の DHCP リレーが、機能の一覧に出ていなかった（2026-10-11 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 機能の一覧（`configparse/features.py`）は Junos の DHCP リレーを
  「未確認」として拾っておらず、`forwarding-options dhcp-relay` で中継している Junos の機器でも、一覧に DHCP リレーが出なかった。
- 確かめた資料: Juniper Support Portal「The Dhcp client is assigned an incorrect address by an illegal dhcp server of the same vlan」
  （`set forwarding-options dhcp-relay server-group dhcp-server 10.0.0.2`・`group … active-server-group`・`group … interface irb.100`）、
  Junos OS「DHCP Active Server Groups」（server-group にサーバーのアドレスを足し、active-server-group で使う）。
- 直したこと: `backend/app/services/configparse/features.py` の DHCP リレーの規則に、server-group のアドレスの行を足した（転送先として出す）。
  冒頭の「未確認」から外した。試験は `test_features.py` の `test_junos_dhcp_relay_server_group_is_found_with_its_server`。
- 見なかったこと: ELS 以前の EX の書き方（`set forwarding-options helpers bootp server …`）。資料に当たっていないので拾っていない。

## Catalyst の 5G 口 `Fi` と 100G 口 `Hu` の略記を、正式名に寄せていなかった（2026-10-12 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 口の名前の正規化（`configparse/ifname.py`）は `Hun` だけを
  HundredGigE に寄せ、`Hu1/1/1`・`Fi1/0/1` はそのまま残していた。`show interfaces status` や CDP の略記で書かれた口が、
  Config の正式名（HundredGigE1/1/1・FiveGigabitEthernet1/0/1）と突き合わなかった。
- 確かめた資料: Cisco「Interface and Hardware Components Configuration Guide, Cisco IOS XE Cupertino 17.9.x (Catalyst 9300 Switches)」
  Configuring Interface Characteristics（5-Gigabit Ethernet (FiveGigabitEthernet or fi)、100-Gigabit Ethernet (HundredGigE or hu)）、
  「Interface and Hardware Components Configuration Guide, Cisco IOS XE Dublin 17.10.x (Catalyst 9400 Switches)」の表示例（Hu3/0/5）。
- 直したこと: `backend/app/services/configparse/ifname.py` の略記の表に `fi`・`hu` を足した。試験は `test_ifname.py` の
  `test_five_and_hundred_gig_short_names_are_expanded`。
- 見なかったこと: 表のほかの略記（xge・mgmt など）。冒頭の「ほかの略記は未確認」はそのまま残した。

## Junos の `vlan members` に範囲（10-20）で書いた VLAN を、黙って捨てていた（2026-10-12 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: Junos の読み手（`configparse/juniper.py`）は `vlan members` の値を
  番号・VLAN 名・all の3つだけ読み、`10-12` のような範囲を捨てていた。`members [ 10-12 23 27-28 ]` のトランクが VLAN 23 だけを
  通すことになり、許可 VLAN の食い違いや「VLAN が届いていない」の判断を誤らせていた。
- 確かめた資料: Juniper「members」（`[edit interfaces interface-name unit logical-unit-number family ethernet-switching vlan]`、
  `members [ (all | names | vlan-ids) ]`。vlan-ids は「10–20 or 10–20 23 27–30」のように範囲で書ける）。
- 直したこと: `backend/app/services/configparse/juniper.py` で範囲の語を `vlanlist.parse_ranges` で開くようにした。
  `vlanlist.py` の「Junos の vlan members は未確認」を外した。試験は `test_configparse_juniper.py` の
  `test_vlan_members_reads_ranges_of_vlan_ids`。

## H3C Comware の集約口の略記 BAGG・RAGG を、正式名に寄せていなかった（2026-10-12 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: 口の名前を突き合わせる `configparse/ifname.py` が `BAGG1` を
  そのまま返し、Config の `Bridge-Aggregation1` と別の口として扱っていた。`RAGG` と正式名の `Route-Aggregation` も表に無かった。
  H3C の `display interface brief` や `display link-aggregation` は略記で書くので、表示の出力と Config の口が結び付かない。
- 確かめた資料: H3C「S9820-8M[S6880] Command References, R1116Pxx-6W100」04-Layer 2—LAN Switching Command Reference の
  display link-aggregation summary（Aggregate Interface Type の凡例「BAGG -- Bridge-Aggregation, ... RAGG -- Route-Aggregation」、
  出力例 `BAGG20`・`RAGG10`）。MSR（V9）の Command Reference にも同じ凡例がある。
- 直したこと: 略記の表に `route-aggregation`・`bagg`・`ragg` を足した。試験は `test_ifname.py` の
  `test_comware_aggregate_short_names_are_expanded`。
- 見なかったこと: H3C の `FGE`・`HGE`・`WGE`・`MGE`。資料の出力例には `WGE1/0/1` と `Twenty-FiveGigE1/0/1` が並ぶが、
  対応を書いた表に当たれなかったので未確認のまま寄せていない。

## Arista の `show ip interface brief` を、見出しの形が違うために一切読んでいなかった（2026-10-12 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `configparse/linkstate.py` の冒頭は「Cisco / Arista / NEC IX の
  show ip interface brief を読む」と書いていたが、見出しの判定は Cisco の `Interface  IP-Address ... Status  Protocol` で
  行が終わる形だけだった。Arista の見出しは `IP Address`（空白）で、Protocol の後ろに `MTU` が付くので当たらず、
  Arista の表は黙って読み飛ばしていた。線の上下（link_state）が空のままになる。
- 確かめた資料: Arista「EOS 4.36.2F User Manual - IPv4」の show ip interface brief の出力例
  （`Interface  IP Address  Status  Protocol  MTU`、行は `Vlan901  170.33.254.1/30  up  up  9212`）。
- 直したこと: Arista の見出しを別に判定し、行の末尾の MTU を外してから Cisco と同じ読み方に渡すようにした。
  冒頭の「確かめた表／未確認の表」も直した。試験は `test_linkstate.py` の
  `test_arista_ip_interface_brief_with_the_mtu_column_is_read`。
- 見なかったこと: Status の値。出力例には up しか無いので、up / down 以外の言葉は未確認のまま（Cisco と同じく上下を決めずに飛ばす）。
  新しい EOS で列が増えた形（Owner など）にも当たれていない。
- ついでに分かったこと: PAN-OS の DHCP の行は、公式の資料（Configure an Interface as a DHCP Server / DHCP Relay）が
  画面の操作の説明だけで、CLI の行の形に当たれなかった。features.py の「未確認」はそのまま。

## Cisco の `show interfaces` で、名前と番号の間が空いた段落（`Ethernet 0 is up`）を読めていなかった（2026-10-13 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `configparse/linkstate.py` の段落の1行目の判定が、口の名前を
  空白の無い1語（`GigabitEthernet1/0/1`）と決めていた。Cisco の公式の出力例は `Ethernet 0 is up, line protocol is up` の形で、
  これだと線の上下も、続く行の CRC の数も読まれなかった。
- 確かめた資料: Cisco「Cisco IOS Interface and Hardware Component Command Reference」の show interfaces の出力例
  （`Ethernet 0 is up, line protocol is up`、`0 input errors, 0 CRC, 0 frame, 0 overrun, 0 ignored, 0 abort`）と、
  show interfaces serial の項目の説明（`is {up | down}` / `is administratively down`、`line protocol is {up | down}`）。
- 直したこと: 名前の後ろに「空白と数字で始まる番号」を1つだけ許すようにした。空白の無い名前は今までどおり読む。
  冒頭の「未確認の表」から Cisco の段落を外した（NEC IX の段落は未確認のまま）。試験は `test_linkstate.py` の
  `test_show_interfaces_paragraph_with_a_space_before_the_number_is_read`。
- 見なかったこと: IOS XE の新しい版の項目の説明の表。公式の PDF（Catalyst 3850）は取りに行くと 504 で読めなかった。

## 見張り: H3C Comware 7 の口の DHCP サーバーは既定で動き、`dhcp select server` が Config に出ない（2026-10-13 JST）

- 確かめたこと: Comware 7 は口の既定が DHCP サーバーの動き方なので、Config には `dhcp select server` が出ないことがある。
  機能の一覧（`configparse/features.py`）は `dhcp server ip-pool` の塊で DHCP サーバーとして拾っており、直す所は無かった。
  直す前のコードで通る見張りの試験として足した。
- 確かめた資料: H3C「SR6600[SR6600-X] Routers Command Reference (V7)-R8149-6W100」06-Layer 3—IP Services Command Reference の
  03-DHCP commands（dhcp select の Default「The interface operates in the DHCP server mode and responds to DHCP requests ...」、
  dhcp enable の Default「DHCP is disabled」、Usage guidelines「DHCP related configuration takes effect only after you enable DHCP」）。
- 試験: `test_features.py` の `test_h3c_comware7_dhcp_server_without_dhcp_select_is_found`。
- 気付いたが手を付けなかったこと: `dhcp enable` が無い Config でも、`dhcp server ip-pool` があれば DHCP サーバーとして載せる。
  資料どおりなら動いていないので言い過ぎになる。ただ同じ行の判定はほかの機種と共有していて、直すと範囲が広がるので見送った。
- 同じ周で見送ったもの: FortiGate の `get system interface physical` と Aruba CX の `show interface brief` は、
  公式の出力例に当たれなかった（第三者の作例とコミュニティの投稿だけ）。linkstate.py の「未確認」はそのまま。

## H3C Comware の 25G 口の略記 WGE と、正式名 Twenty-FiveGigE を同じ口に寄せていなかった（2026-10-13 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `configparse/ifname.py` の略記の表に、H3C の正式名
  `Twenty-FiveGigE`（ハイフン入り）も略記の `WGE` も無かった。表示の出力の `WGE1/0/3` と Config の
  `interface Twenty-FiveGigE1/0/3` が、別の口として扱われていた。
- 確かめた資料: H3C「S6850 & S9850 Switch Series Command References, Release 655x-6W100」03-Ethernet interface commands
  （`display interface twenty-fivegige 1/0/3 brief description` の出力が `WGE1/0/3`。display counters などの
  Interface 列の説明は「Abbreviated interface name」）。
- 直したこと: 略記の表に `twenty-fivegige` と `wge` を足し、Cisco の `TwentyFiveGigE`・`Twe` と同じ正式名に寄せた。
  試験は `test_ifname.py` の `test_comware_twenty_five_gig_short_name_is_expanded`。
- 見なかったこと: H3C の `FGE`・`HGE`・`MGE`。同じ資料の見えた範囲には出力例が無く、未確認のまま。
- 同じ周で見送ったもの: H3C の `dhcp enable` が無い Config を DHCP サーバーとして載せる件。判定の `dhcp server ip-pool` は
  NEC QX と共用で、QX の Config 例には `dhcp enable` が無い。QX の資料にこの環境から届かないので、条件を足すと QX を崩しうる。

## H3C Comware の 40G・100G 口の略記 FGE・HGE を、正式名に寄せていなかった（2026-10-13 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `configparse/ifname.py` の略記の表に `FGE`・`HGE` が無く、
  H3C の 40G の正式名 `FortyGigE` も無かった（`fortygig` は語がちょうど一致するときしか当たらない）。表示の出力の
  `FGE1/1/3`・`HGE1/0/3` と、Config の `FortyGigE1/1/3`・`HundredGigE1/0/3` が別の口として扱われていた。
- 確かめた資料: H3C「S7500E-XS Command References, R2418P05-6W100」01-Ethernet interface commands
  （`display interface fortygige 1/1/3 brief description` の出力が `FGE1/1/3`、Interface 列の説明は「Abbreviated interface name」）と、
  H3C「S12500R Switch Router Series Command References (R36xx)-6W101」03-Ethernet interface commands
  （`display interface hundredgige 1/0/3 brief description` の出力が `HGE1/0/3`）。
- 直したこと: 略記の表に `fortygige`・`fge`・`hge` を足し、Cisco の `Fo`・`FortyGigabitEthernet` と `Hu`・`HundredGigE` と
  同じ正式名に寄せた。試験は `test_ifname.py` の `test_comware_forty_and_hundred_gig_short_names_are_expanded`。
- 見なかったこと: H3C の `MGE`（管理口 M-GigabitEthernet）。対になる出力例に当たれず、未確認のまま。

## Cisco IOS の `no exec-timeout`（時間切れの無い画面）を指摘していなかった（2026-10-13 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `configcheck.py` の「入ったままの画面が、いつまでも閉じません」は
  `exec-timeout 0 0` だけを見ていた。Cisco は `no exec-timeout` も「時間切れなし」と定めているのに、こちらは黙って通していた。
  Cisco の書き方の行には出典も付いていなかった。
- 確かめた資料: Cisco「Cisco IOS Configuration Fundamentals Command Reference」D through E の exec-timeout
  （Usage Guidelines「To specify no timeout, enter the no form of this command.」、Command History 15.0(1)SY2
  「The no form of the command updated to configure an exec-timeout of 0 0」、Command Default は 10 分）。
- 直したこと: 規則の正規表現に `no exec-timeout` を足し、Cisco の行に出典を書いた。`exec-timeout 10 0` のように時間を決めた行は
  今までどおり言わない。試験は `test_configcheck.py` の `test_no_exec_timeout_is_called_out_like_zero_zero`。
- 見なかったこと: Huawei の `idle-timeout 0 0`（同じ規則に当たる）。公式のページが画面の中で後から読み込む作りで、
  この環境の取得では中身が空だった。検索の抜粋では「0 0 で時間切れなし」とあるが、本文に当たれていないので未確認のまま。
- 同じ周で見送ったもの: H3C の管理口の略記 `MGE`。対になる出力例に当たれなかった。

## 見張り: Cisco の `switchport mode dynamic auto / desirable` の規則に出典が無かった（2026-10-14 JST）

- 確かめたこと: `configcheck.py` の「相手任せでトランクになる口があります」は、Cisco の行に出典が無かった。公式資料で確かめ、
  `dynamic auto`・`dynamic desirable` を指摘して `access`・`trunk` を言わない今の形で合っていた。直す所は無く、
  直す前のコードで通る見張りの試験として足し、規則に出典を書いた。書いていない既定の dynamic auto は、既にある別の規則
  （「相手次第でトランク」）で見ている。
- 確かめた資料: Cisco「Command Reference, Cisco IOS XE Cupertino 17.9.x (Catalyst 9300 Switches)」Layer 2/3 Commands の
  switchport mode（`switchport mode { access | dynamic { auto | desirable } | trunk }`、Command Default は dynamic auto）。
- 試験: `test_configcheck.py` の `test_both_negotiating_modes_are_called_out_and_fixed_modes_are_not`。
- 同じ周で見送ったもの: Cisco の `transport input ssh telnet` のように telnet を後ろに書いた行を、今の規則
  （`transport input (?:all|telnet)`）は当てない。複数の方式を並べて書けることを公式の資料で確かめられなかった
  （IOS 12.2 Terminal Services Configuration Guide は `transport input { ... | ssh | telnet | ... }` の形だけ）ので、直していない。

## 見張り: Cisco の `ip http server` の規則に出典が無かった（2026-10-14 JST）

- 確かめたこと: `configcheck.py` の「管理用の web が開いています」は、Cisco の行に出典が無かった。公式資料で確かめ、
  `ip http server` を指摘し、`ip http secure-server`（https）と `no ip http server` を言わない今の形で合っていた。
  直す所は無く、直す前のコードで通る見張りの試験として足し、規則に出典を書いた。直し方の文（no ip http server）も資料と同じ。
- 確かめた資料: Cisco「Cisco IOS HTTP Services Command Reference」clear ip http client cookie through show ip http server
  secure status の ip http server（To disable the HTTP server, use the no form of this command）と ip http secure-server
  （When enabling an HTTPS server, you should always disable the standard HTTP server ... using the no ip http server command）。
- 試験: `test_configcheck.py` の `test_ios_http_server_is_called_out_and_the_secure_server_is_not`。

## Cisco の `show interfaces status err-disabled` の表を、見出しの形が違うために読んでいなかった（2026-10-14 JST）

- 起きていたこと（直す前のコードで試験が落ちることを確かめた）: `configparse/linkstate.py` の `show interfaces status` の見出しの判定は
  `Port Name Status Vlan Duplex Speed Type` の形だけだった。err-disabled に絞った表は見出しが `Port Name Status Reason` で、
  当たらずに黙って読み飛ばしていた。止まった口を洗い出すために打つ表なのに、口の状態が空のままになる。
- 確かめた資料: Cisco「Command Reference, Cisco IOS XE Cupertino 17.9.x (Catalyst 9300 Switches)」Interface and Hardware Commands の
  show interfaces（`status [ err-disabled | inactive ]`、出力例 `Port Name Status Reason` / `Gi1/0/2 err-disabled gbic-invalid` /
  `Gi2/0/3 err-disabled dtp-flap`）。
- 直したこと: 見出しの判定に `Port Name Status Reason` の形を足した。行の読み方は今までの `_read_status` のまま（行の中から
  Status の言葉を探す）。試験は `test_linkstate.py` の `test_show_interfaces_status_err_disabled_with_the_reason_column_is_read`。
- 見なかったこと: `show interfaces status inactive` の出力例。資料には選択肢の説明（Displays interfaces in an inactive state）だけで、
  出力例が無かった。
- 同じ周で見送ったもの: Cisco の `no login` の規則の出典。Security Command Reference の login (line) の項に、この環境から当たれなかった
  （新しい版の章は見つからず、古い版は 404 か 504）。
