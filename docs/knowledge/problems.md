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
