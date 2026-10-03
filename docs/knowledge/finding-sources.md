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

Config 点検の資料はどれも Cisco のもの。点検が Cisco 型の書き方（`switchport`・`ip dhcp snooping` など）の行を見て出す指摘なので、
その書き方の資料を添えている。同じ書き方の他社（Arista など）で既定が違うかは確かめていない。

## 次に広げる順

1. Config 点検の残り。「STP のモードを決めていません」は既定のモードが機種と版で違い、資料で確かめていないので付けていない。
   「電話をつなぐ口のポートセキュリティの上限」は既定の 1 を資料の既定値表で確かめてから付ける。VLAN・STP・冗長・ログの点検の `FindingRead` に欄を足す。
2. 既定値ではなく「機種の振る舞い」に拠る指摘（HSRP は既定で取り返さない、など）。出典はいまコメントにだけある。
3. 画面以外に指摘を出す場所ができたら（報告書の書き出しなど）、そこにも同じ行を出す。

試験: `backend/tests/test_l3review.py`、`backend/tests/test_linkcheck.py` の末尾、`frontend/src/components/l3Panel.test.ts`。
