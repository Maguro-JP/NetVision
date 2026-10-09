# 指摘に出典を添える

点検の指摘（`Finding`、`backend/app/services/reviewnote.py`）に、その判断が拠った資料の名前を持たせる。
ここでは形と、どこに出るか、どこまで付けたかを書く。

## なぜ要るか

書いていない設定を既定値で読んで出した指摘は、根拠の行（`evidence`）だけでは確かめられない。
既定値は Config のどこにも書いていないので、「優先度 100（既定）」と見ても、読み手は 100 が正しいかを
確かめる先を持たない。資料の名前があれば、そこを開けば済む。

## 形

- `Finding.sources: list[str]`。既定は空。資料の名前を、文書名と箇所まで短く書いた文字列で持つ。
- 文字列は `backend/app/services/defaults.py` に、既定値と並べて置く（`FHRP_PRIORITY_SOURCE` など）。
  既定値の出典のコメントと同じ資料を、画面に出す長さに縮めたもの。既定値を変えたら、同じ場所で一緒に直す。
- 資料に届いていない既定は、確かめたふりをせず「未確認: …」で始まる文字列にする
  （`defaults.static_distance_source` が、表に無い機種でそう返す）。
- 付けるのは、既定値に拠ったときだけ。両方とも値を書いてある組の指摘には付けない
  （例: 両端とも OSPF の認証の掛け方を書いてあって違うとき）。

URL ではなく名前にした。資料の URL は版ごとに変わり、届かない環境もある。名前なら版が変わっても探せる。

## どこに出るか

| 経路 | 出典の扱い |
|---|---|
| `GET /api/v1/devices/{id}/l3`（`config_plans.py` の `FindingRead`） | `sources` を返す |
| `GET /api/v1/topology/link-check`（`topology.py` の `LinkFindingRead`） | `sources` を返す |
| 画面: L3 の指摘（`frontend/src/components/L3Panel.tsx`） | 「見たところ」の下に「出典: …」の行。無ければ行を出さない |
| 画面: 結線の点検（`frontend/src/components/LinkCheckPanel.tsx`） | 同上（`sourcesLine` を共有） |
| `POST /api/v1/config-check`（`configcheck.py` の `FindingRead`） | `sources` を返す（`findings` と `differences` の両方） |
| 画面: Config 点検（`frontend/src/pages/ConfigCheckPage.tsx`） | 「見たところ」の下に「出典: …」の行 |
| VLAN・STP・冗長・ログの点検 | まだ返さない。返す形（`FindingRead`）に欄が無い |
| エクスポート（`exporter.py`、`topology_excel.py`、`topology_export.py`） | 指摘をそもそも書き出していない。対象外 |
| ことばで聞く（`askback.py`） | `evidence` の先頭3行だけを使う。出典は使っていない |

API の欄は既定を空にしてあり、古い画面が受け取っても壊れない。画面の型は `sources?: string[]` で、古い応答にも耐える。

## いま出典が付く指摘

| 指摘 | 付く条件 | 出典 |
|---|---|---|
| l3review「…への経路が同じ重みで複数あります」 | 距離を書いていない経路が1本でもある | 機種ごとの静的経路の既定（`static_distance_source`） |
| l3review「HSRP/VRRP グループ … の優先度が同じです」 | どちらかが優先度を書いていない、または VRRP の持ち主で 255 | `FHRP_PRIORITY_SOURCE`、`VRRP_OWNER_PRIORITY_SOURCE` |
| linkcheck「片側だけ速度/二重を固定しています」 | 常に（片側が自動＝既定） | `NEGOTIATED_SOURCE` |
| linkcheck「OSPF の合言葉の掛け方が両端で違います」 | どちらかが掛けていない（書いていない、または null） | `OSPF_AUTH_SOURCE` |
| configcheck「VLAN を書いていないアクセスの口が、既定の VLAN 1 に乗っています」 | 常に | `ACCESS_VLAN_SOURCE` |
| configcheck「DHCP の見張りで、信用する口を1つも決めていません」 | 常に（口は既定で信用しない） | `DHCP_SNOOPING_UNTRUSTED_SOURCE` |
| configcheck「見張りを入れた口から、上位で落とされる形の DHCP が出ます」 | 常に（option 82 は既定で有効） | `DHCP_OPTION82_SOURCE` |
| configcheck「ARP の見張りで、信用する口を1つも決めていません」 | 常に（口は既定で信用しない） | `ARP_INSPECTION_UNTRUSTED_SOURCE` |
| configcheck「HSRP のグループ番号が、版1で使える範囲を超えています」 | 常に（版を書かなければ版1） | `HSRP_VERSION_SOURCE` |
| configcheck「ポートセキュリティの、破ったときの動きを決めていません」 | 常に（既定は shutdown） | `PORT_SECURITY_VIOLATION_SOURCE` |
| configcheck「電話をつなぐ口で、覚えられる数が足りません」 | 上限を書いていない口が1つでもある（既定は1） | `PORT_SECURITY_MAXIMUM_SOURCE` |
| configcheck「STP のモードを決めていません」 | 既定を資料で確かめた機種だけ（IOS: Catalyst 9300 は Rapid PVST+、NX-OS: Nexus 9000 は Rapid PVST+、Arista EOS: MSTP、AlliedWare Plus: RSTP） | `stp_mode_source` |
| policyreview「… とは、このままでは接続が張れません」 | 常に（`disable-connected-check`・`ttl-security hops` を書いてあれば指摘そのものを出さない） | `EBGP_CONNECTED_CHECK_SOURCE` |
| l3review「HSRP グループ … は、一度切り替わると戻りません」 | 常に（preempt の既定は無効） | `HSRP_PREEMPT_SOURCE` |
| l3review「OSPF の口が全部 passive のままです」 | OSPF のときだけ（資料で確かめたのは OSPF の hello） | `OSPF_PASSIVE_SOURCE` |
| configcheck「DHCP の見張りを、どの VLAN にも掛けていません」 | 常に（VLAN に掛けるまで働かない） | `DHCP_SNOOPING_VLAN_SOURCE` |
| configcheck「ARP の見張りに、判断のもとがありません」 | 常に（判断は見張りの対応表か ARP ACL に拠る） | `ARP_INSPECTION_BINDING_SOURCE` |
| asareview「security-level が同じ口が … つあります」 | 常に（`same-security-traffic permit inter-interface` を書いてあれば指摘そのものを出さない） | `ASA_SAME_SECURITY_SOURCE` |
| asareview「外に面した … に、入る側の制限がありません」 | 常に（低い security-level から高い側へは既定で通さない） | `ASA_LOWER_TO_HIGHER_SOURCE` |

Config 点検の資料はどれも Cisco のもの。点検が Cisco 型の書き方（`switchport`・`ip dhcp snooping` など）の行を見て出す指摘なので、
その書き方の資料を添えている。同じ書き方の他社（Arista など）で既定が違うかは確かめていない。

## 次に広げる順

1. VLAN・STP・冗長・ログの点検（`topology.py` の各 `FindingRead`）。いまはどの指摘も既定値に拠った資料を持たないので、欄だけ通しても何も出ない。既定値に拠る指摘を見つけたときに、欄と一緒に足す。
   STP のモードの既定を、残りの機種でも資料で確かめる（Aruba CX・AOS-S・AlaxalA・Dell OS10・Cisco Business・Huawei・H3C・NEC QX は確かめた。AOS-S は 2026-10-10 JST）。
   VLAN・STP・冗長・無線の点検（vlanreview・stpreview・hareview・wirelessreview）を洗った範囲では、既定に拠る指摘は見つからなかった（2026-10-03 JST）。
2. 既定値ではなく「機種の振る舞い」に拠る指摘（HSRP は既定で取り返さない、など）。出典はいまコメントにだけある。
3. 画面以外に指摘を出す場所ができたら（報告書の書き出しなど）、そこにも同じ行を出す。

試験: `backend/tests/test_l3review.py`、`backend/tests/test_linkcheck.py` の末尾、`frontend/src/components/l3Panel.test.ts`。

## 既定を明示で変える書き方を読んでいるか（2026-10-03 JST に確かめた範囲）

既定に拠る指摘は、既定を変える書き方を読まずにいると、直してある機器にも出てしまう（ASA の same-security-traffic で起きた）。
policyreview.py と l3review.py の既定に拠る指摘を見た。

| 指摘 | 既定を変える書き方 | 状態 |
|---|---|---|
| policyreview「このままでは接続が張れません」（update-source と eBGP） | `ebgp-multihop`、`disable-connected-check`、`ttl-security hops` | 後の2つを読んでいなかった。直した |
| l3review「既定の経路が見当たりません」 | 動的な経路のプロトコル（OSPF・BGP に加えて EIGRP・RIP・IS-IS） | EIGRP・RIP・IS-IS を見ていなかった。直した |
| l3review「一度切り替わると戻りません」（HSRP） | `standby N preempt` | 読んでいる |
| l3review「口が全部 passive のままです」 | `no passive-interface X` | 読んでいる |
| asareview「security-level が同じ口が」 | `same-security-traffic permit inter-interface` | #146 で直した |
| configcheck「VLAN だけ書いて mode を書いていない口が、相手次第でトランクになります」 | 機種の既定（Arista EOS は static access で DTP が無い） | Arista にも出していた。既定の dynamic auto を確かめた IOS だけに絞った |
| configcheck「VLAN を書いていないアクセスの口が、既定の VLAN 1 に乗っています」 | `switchport access vlan` | 読んでいる |
| configcheck「DHCP の見張りで、信用する口を1つも決めていません」「ARP の見張りで…」 | 口ごとの `ip dhcp snooping trust`・`ip arp inspection trust` | 読んでいる |
| configcheck「上位で落とされる形の DHCP が出ます」 | `no ip dhcp snooping information option`、`ip helper-address` | 読んでいる |
| configcheck「HSRP のグループ番号が、版1で使える範囲を超えています」 | 口ごとの `standby version 2` | 読んでいる |
| configcheck「ポートセキュリティの、破ったときの動きを決めていません」 | `switchport port-security violation …` | 読んでいる |
| configcheck「電話をつなぐ口で、覚えられる数が足りません」 | 口全体の `switchport port-security maximum N` | VLAN ごとの行（`maximum N vlan voice`）だけの口で、全体の上限が既定の1のままなのを見逃していた。全体の行だけを読むよう直した |
| configcheck「STP のモードを決めていません」 | `spanning-tree mode …` | 読んでいる |
| configcheck「タグ無しの VLAN を、許可の一覧から外しています」 | 機種の既定（AlliedWare Plus は既定のタグ無し VLAN 1 を一覧に入れなくても一員になる） | Allied にも出していた。Allied の VLAN 1 では言わない |

### Cisco と同じ書き方の他社で出る指摘（2026-10-03 JST に fixture で流した範囲）

| 機種（fixture） | 出た指摘 | 判断 |
|---|---|---|
| Allied x530（allied-x530-01.txt） | タグ無しの VLAN を、許可の一覧から外しています | 誤り。直した（Allied VLAN Feature Overview C613-22001-00 REV P） |
| Allied x530 | 端末用の口に、BPDU guard が入っていません | 正しい。口ごとの書き方は拾える。全体の `spanning-tree portfast bpdu-guard` を拾えていなかったので直した（Allied STP Feature Overview C613-22026-00 REV A） |
| Allied x530・Arista | トランクのタグ無し VLAN が 1 のままです / STP のモードを決めていません | 機種に依らない勧め。STP のモードは Arista（MSTP）と Allied（RSTP）に出典が付く |
| Dell OS10・Cisco SB・Alaxala | fixture が無い | 未確認 |

