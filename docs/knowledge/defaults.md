# 既定値と、それに頼っている点検

書いていない設定は、既定値で動く。点検が「書いていない」を「無い」と取り違えると、正しい設定に指摘を出す。
9 月後半の誤った指摘（docs/knowledge/problems.md）の多くがこの形だった。

既定値は `backend/app/services/defaults.py` に集め、出典を定数の注記に残す。点検はそこから引く。
新しく既定値に頼る点検を書くときは、先に defaults.py に定数と出典を足し、この表に1行足す。

## defaults.py に置いたもの

| 既定値 | 値 | 頼っている点検 | 出典 |
|---|---|---|---|
| HSRP・VRRP の優先度 | 100 | l3review「優先度が同じです」「一度切り替わると戻りません」 | Cisco Configuring VRRP（IOS XE）、FHRP Command Reference |
| VRRP の持ち主の優先度 | 255 | l3review「優先度が同じです」 | Cisco Configuring VRRP の Object Tracking |
| 静的経路の距離 | Cisco・AOS-CX・Arista 1、Huawei・H3C 60、FortiGate 10、Junos 5 | l3review「同じ重みで複数あります」 | Cisco IP Routing Configuration Guide 17.x の Table 1、Huawei / H3C の ip route-static、FortiOS CLI Reference の config router static、Juniper Configure Static Routes、AOS-CX 10.13 IP Routing Guide の ip route distance、Arista EOS User Manual「IPv4」の ip route |
| 速度・二重 | 自動（`auto` と同じ） | linkcheck「片側だけ固定しています」 | Cisco Interface Characteristics Configuration Guide 17 |
| OSPF の口の認証 | 掛けない（`null` と同じ） | linkcheck「合言葉の掛け方が両端で違います」 | Cisco Configure Authentication in OSPF |

上の出典は、指摘の `sources` として画面にも出す（資料の名前は defaults.py の `*_SOURCE`）。既定値を変えたら名前も一緒に直す。形は [finding-sources.md](finding-sources.md)。

## 読み手の側で既定値を入れているもの（defaults.py には置いていない）

読み手が機種ごとの書き方を読む場所で決めているので、そのまま残している。

| 既定値 | 置き場所 | 出典 |
|---|---|---|
| FortiGate の DHCP の status（enable）・dns-service（specify） | configparse/fortinet.py | FortiOS CLI Reference |
| Huawei の gateway-list は自動で配らない | configparse/huawei.py（gateway_reserved） | Huawei S9300 Configuration Guide |
| Cisco の DHCP の host は親の network を受け継ぐ | configparse/policy.py | Cisco IOS XE 17.x DHCP |
| Junos の古い形の DHCP は全体の値を受け継ぐ | configparse/juniper.py | Juniper Legacy DHCP and Extended DHCP |

## 未確認

- 静的経路の距離の NEC QX・NEC IX の既定（資料に届かない）。いまは Cisco の 1 として扱う
- Aruba CX の STP の既定のモード（AOS-CX の資料はこの環境から 403 で開けない。検索結果の要約では MSTP だが、原文を見ていない）
- `ip route-static default-preference` で既定を変えた機器で、preference を書いていない経路が変更の前と後のどちらに足されたか（Config からは決まらない。l3review はその経路を含む組を判定しない）
