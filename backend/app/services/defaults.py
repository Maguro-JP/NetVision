"""書いていない設定が、実際にどの値で動くか（既定値）を1か所に置く。

点検は「書いていない」を「無い」と取り違えやすい。既定値のまま書かない設定は正しい設定で、
そこへ指摘を出すと、直す必要の無いものを直させてしまう。既定値をここに集め、出典と一緒に持つ。
点検はここから引き、点検ごとに既定値を書き直さない。

どの点検がどの既定値に頼っているかの一覧は docs/knowledge/defaults.md。
"""

from __future__ import annotations

from typing import Protocol


class _Redundancy(Protocol):
    interface: str
    virtual_ip: str | None
    priority: int | None
    protocol: str


class _Port(Protocol):
    name: str
    ip_address: str | None
    extra_addresses: list[str]


#: HSRP・VRRP の優先度を書かないときの値。
#: 出典: Cisco「Network Services Configuration Guide - Configuring VRRP」（IOS XE）の VRRP Router
#: Priority and Preemption（「The default priority is 100.」）、Cisco IOS First Hop Redundancy
#: Protocols Command Reference の standby priority（既定 100）
FHRP_PRIORITY = 100

#: VRRP で、仮想アドレスが自分の口のアドレスと同じ機器（持ち主）の優先度。
#: 出典: 同ガイドの Configuring VRRP Object Tracking（「If a VRRP group is the IP address owner,
#: its priority is fixed at 255」）
VRRP_OWNER_PRIORITY = 255

#: 静的経路の距離（administrative distance）を書かないときの値。Cisco の値。
#: 出典: Cisco「IP Routing Configuration Guide, Cisco IOS XE 17.x - Basic IP Routing」の
#: Table 1「Default Administrative Distances」（Static route: 1）
STATIC_ROUTE_DISTANCE = 1

#: 機種ごとの静的経路の距離の既定。ここに無い機種は STATIC_ROUTE_DISTANCE（Cisco の 1）。
#: 出典:
#: - Huawei: 「SME Network Fat AP and Cloud AP V200R025C10 Command Reference」の ip route-static
#:   （「If no preference is configured for a static route, the static route uses the default
#:   preference 60.」）、「S7700 V200R023C10 Command Reference」の ip route-static
#:   default-preference（既定 60）
#: - H3C: 「02-Static routing commands」（Comware 7）の ip route-static（preference の既定 60）
#: - FortiGate: FortiOS CLI Reference「config router static」（7.0.1。distance の既定 10）
#: - Junos: Juniper「Configure Static Routes」（「In Junos OS, static routes have a global
#:   preference (administrative distance) of 5.」）。`static defaults preference` で変えた機器は
#:   読み手（configparse/routing.py の _junos_static）が経路ごとの距離として入れる
#: Arista は Cisco と同じ 1（EOS User Manual「IPv4」の ip route: By default, the administrative
#: distance assigned to static routes is 1）なので、上の表には置かず出典だけを持つ。
#: - NEC QX: 「QX-S1100G シリーズ コマンドマニュアル 1.9版」セクション4 2.1.2 ip route-static
#:   （「preference preference: … デフォルトは 60 です。」）、
#:   2.1.3 ip route-static default-preference（デフォルト 60）
#: NEC IX は Cisco と同じ 1 なので、上の表には置かず出典だけを持つ。出典は NEC
#: 「IX-R/IX-V コマンドリファレンス 1.3」IPv4 編のスタティックルート追加
#: （DISTANCE のデフォルト値: 1）、
#: 「IX-R/IX-V 機能説明書」2.15 ルーティングの設定の既定のディスタンス表（Static: 1）。
#: 未確認: IX2000/IX3000 の資料（IX-R/IX-V の資料は IX2000/IX3000 との差分を項ごとに書くが、
#: ip route の項には差分の記載が無い）。
#: - AlaxalA: 「AX3660S コンフィグレーションコマンドレファレンス Vol.2」Ver.12.2 の ip route
#:   （<distance> の「本パラメータ省略時の初期値 2」）、
#:   「コンフィグレーションガイド Vol.3」Ver.12.2 の表24-3 ディスタンスのデフォルト値
#:   （スタティック経路 2）
#: `ip route-static default-preference` で既定を変えた機器は、書き換えた後に足した経路にだけ効くため
#: preference を書いていない経路の距離が決まらない。l3review はその経路を含む組を判定しない
STATIC_ROUTE_DISTANCE_BY_VENDOR: dict[str, int] = {
    "huawei": 60,
    "h3c": 60,
    "fortinet": 10,
    "juniper": 5,
    "nec_qx": 60,
    "alaxala": 2,
}


#: 指摘に添える資料の名前。読み手が確かめに行けるよう、文書名と箇所まで書く。
#: 上のコメントの出典と同じものを、画面に出す長さに縮めてある
FHRP_PRIORITY_SOURCE = (
    "Cisco Network Services Configuration Guide（IOS XE）Configuring VRRP / Configuring HSRP:"
    " 優先度の既定は 100"
)
VRRP_OWNER_PRIORITY_SOURCE = (
    "Cisco Network Services Configuration Guide（IOS XE）Configuring VRRP Object Tracking:"
    " 仮想アドレスの持ち主は優先度 255"
)
_STATIC_DISTANCE_SOURCES: dict[str, str] = {
    "cisco_ios": "Cisco IP Routing Configuration Guide, IOS XE 17.x Basic IP Routing の Table 1"
    "「Default Administrative Distances」: 静的経路は 1",
    "aruba_cx": "AOS-CX 10.13 IP Routing Guide の ip route（distance の既定 1）",
    "arista": "Arista EOS User Manual「IPv4」の ip route: 静的経路の距離の既定は 1",
    "huawei": "Huawei Command Reference の ip route-static: preference の既定 60",
    "h3c": "H3C Comware 7「02-Static routing commands」の ip route-static: preference の既定 60",
    "fortinet": "FortiOS CLI Reference「config router static」: distance の既定 10",
    "juniper": "Juniper「Configure Static Routes」: 静的経路の preference は 5",
    "nec_qx": "NEC QX-S1100G シリーズ コマンドマニュアル 1.9版 の ip route-static:"
    " preference の既定 60",
    "nec_ix": "NEC IX-R/IX-V コマンドリファレンス 1.3 の ip route（スタティックルート追加）:"
    " distance の既定 1",
    "alaxala": "アラクサラ AX3660S コンフィグレーションコマンドレファレンス Vol.2（Ver.12.2）の"
    " ip route: distance の初期値 2",
}


def static_distance_source(vendor: str | None) -> str:
    """静的経路の距離の既定を、どの資料で確かめたか。届いていない機種は未確認と書く。"""
    found = _STATIC_DISTANCE_SOURCES.get(str(vendor or ""))
    if found:
        return found
    return (
        "未確認: この機種の静的経路の距離の既定は資料で確かめていません"
        f"（{STATIC_ROUTE_DISTANCE} として見ています）"
    )


#: 速度・二重を書かないときに自動で決まること、OSPF の口の認証が既定で掛かっていないことの資料
NEGOTIATED_SOURCE = (
    "Cisco IOS XE 17 Interface Characteristics Configuration Guide の"
    " Default Ethernet interface configuration: 速度も二重も既定は自動"
)
OSPF_AUTH_SOURCE = (
    "Cisco「Configure Authentication in Open Shortest Path First」: 既定は認証なし（Type 0）"
)


#: Config 点検（configcheck.py）が頼る既定。値は点検の側で持つが、画面に出す資料の名前はここに置く。
#: どれも configcheck.py の各点検の docstring・コメントにある出典を、画面に出す長さに縮めたもの
ACCESS_VLAN_SOURCE = (
    "Cisco IOS XE 17 Interface Characteristics Configuration Guide の"
    " Default Ethernet interface configuration: アクセスの口の既定 VLAN は 1"
)
DHCP_SNOOPING_UNTRUSTED_SOURCE = (
    "Cisco Catalyst 3560 Software Configuration Guide 15.0(2)SE"
    "「Configuring DHCP Features and IP Source Guard」: DHCP サーバーへの口は trust が要る"
)
DHCP_OPTION82_SOURCE = (
    "Cisco Catalyst 6500 Sup2T Software Configuration Guide"
    "「Default Configuration for DHCP Snooping」: option 82 の挿入は既定で有効"
)
ARP_INSPECTION_UNTRUSTED_SOURCE = (
    "Cisco Security Configuration Guide, IOS XE 17.17.x（Catalyst 9300）"
    "「Configuring Dynamic ARP Inspection」: 口は既定ですべて untrusted"
)
HSRP_VERSION_SOURCE = (
    "Cisco Network Services Configuration Guide, IOS XE 17.x「HSRP Version 2」:"
    " 既定の版1のグループ番号は 0〜255"
)
PORT_SECURITY_VIOLATION_SOURCE = (
    "Cisco Security Configuration Guide, IOS XE 17.16.x（Catalyst 9500）「Port Security」の"
    " Default Port Security Configuration: violation の既定は shutdown"
)
PORT_SECURITY_MAXIMUM_SOURCE = (
    "Cisco Security Configuration Guide, IOS XE 17.17.x（Catalyst 9500）「Port Security」の"
    " Default Port Security Configuration: 覚えられる MAC の数の既定は1"
)
DTP_DYNAMIC_AUTO_SOURCE = (
    "Cisco IOS XE 17 Interface Characteristics Configuration Guide の"
    " Default Ethernet interface configuration: L2 の口の既定は dynamic auto（DTP を使う）"
)
#: 機種の既定の振る舞いに拠る指摘の資料。値ではなく「書かなければこう動く」を確かめたもの
HSRP_PREEMPT_SOURCE = (
    "Cisco IOS First Hop Redundancy Protocols Command Reference の standby preempt、"
    "Nexus 9000 NX-OS Unicast Routing Configuration Guide「Configuring HSRP」: preempt の既定は無効"
)
DHCP_SNOOPING_VLAN_SOURCE = (
    "Cisco Catalyst 3560 Software Configuration Guide 15.0(2)SE"
    "「Configuring DHCP Features and IP Source Guard」: VLAN に掛けるまで DHCP の見張りは働かない"
)
ARP_INSPECTION_BINDING_SOURCE = (
    "Cisco Security Configuration Guide, IOS XE 17.17.x（Catalyst 9300）"
    "「Configuring Dynamic ARP Inspection」: 判断は DHCP の見張りの対応表か ARP ACL に拠る"
)
OSPF_PASSIVE_SOURCE = (
    "Cisco IP Routing Configuration Guide（IOS XE）「Default Passive Interfaces」:"
    " passive の口では OSPF の hello を出さず、隣ができない"
)
#: eBGP は既定で直結の相手としか張らない。policyreview.py の update-source の指摘が頼る
EBGP_CONNECTED_CHECK_SOURCE = (
    "Cisco IOS IP Routing: BGP Command Reference の neighbor disable-connected-check:"
    " 1ホップの eBGP は既定で相手が同じ帯に直結しているかを確かめる"
)
#: ASA の security-level の既定の扱い。asareview.py が頼る
ASA_SAME_SECURITY_SOURCE = (
    "Cisco ASA Series General Operations CLI Configuration Guide 9.16"
    "「Routed and Transparent Mode Interfaces」: 同じ security-level の口どうしは既定で通れない"
)
ASA_LOWER_TO_HIGHER_SOURCE = (
    "Cisco ASA Series General Operations CLI Configuration Guide 9.16"
    "「Routed and Transparent Mode Interfaces」: 低い security-level から高い側へは既定で通さない"
)
#: 既定のモードは機種と版で違いうる。確かめた機種だけを並べる
STP_MODE_SOURCE = (
    "Cisco Layer 2 Configuration Guide, IOS XE 17.17.x（Catalyst 9300）の"
    " Default Spanning-Tree Configuration: Catalyst 9300 の既定は Rapid PVST+。機種と版で違う"
)
_STP_MODE_SOURCES: dict[str, str] = {
    "cisco_ios": STP_MODE_SOURCE,
    "cisco_nxos": "Cisco Nexus 9000 Series NX-OS Layer 2 Switching Configuration Guide 10.2(x)"
    "「Configuring Rapid PVST+」の Default Settings: 既定は Rapid PVST+",
    "arista": "Arista EOS User Manual「Spanning Tree Protocol」: MSTP is the default STP version",
    "allied": "Allied Telesis STP Feature Overview and Configuration Guide C613-22026-00 REV A:"
    " By default, the device operates in RSTP mode",
    "aruba_cx": "AOS-CX 10.12 Layer-2 Bridging Guide（8400）の spanning-tree mode:"
    " no 形で既定の mstp に戻る",
    "alaxala": "アラクサラ AX3660S コンフィグレーションガイド Vol.1（Ver.12.1）"
    "27.2.2 動作モードの設定:"
    " 動作モードを設定しない場合，pvst モードで動作します",
    "dell_os10": "Dell SmartFabric OS10 User Guide Release 10.6.0「Enable Rapid-PVST」:"
    " By default, Rapid-PVST is enabled",
    "cisco_sb": "Cisco Business Switches 350 Series CLI Guide「Spanning Tree Commands」の"
    " spanning-tree mode: The default is RSTP",
    "huawei": "Huawei S9300 and S9300E V200R008 Configuration Guide - Ethernet Switching"
    "「Configuring Basic MSTP Functions」: By default, the working mode is MSTP",
    "h3c": "H3C「07-Spanning tree configuration」Setting the spanning tree mode:"
    " The default setting is the MSTP mode",
    "nec_qx": "NEC QX-S1100G シリーズ コマンドマニュアル 1.9版 8.1.31 stp mode: デフォルト mstp",
}


def stp_mode_source(vendor: str | None) -> str | None:
    """STP の既定のモードを、どの資料で確かめたか。確かめていない機種は None（出典を出さない）。"""
    return _STP_MODE_SOURCES.get(str(vendor or ""))


def fhrp_priority(entry: _Redundancy, ports: list[_Port]) -> int:
    """実際に効く HSRP・VRRP の優先度。書いていなければ 100、VRRP の持ち主は 255。"""
    if entry.protocol == "vrrp" and entry.virtual_ip:
        port = next((p for p in ports if p.name == entry.interface), None)
        addresses = [port.ip_address, *port.extra_addresses] if port else []
        if any(a and a.split("/")[0] == entry.virtual_ip for a in addresses):
            return VRRP_OWNER_PRIORITY
    return entry.priority if entry.priority is not None else FHRP_PRIORITY


def static_distance(distance: int | None, vendor: str | None = None) -> int:
    """実際に効く静的経路の距離。書いていなければ機種の既定。"""
    if distance is not None:
        return distance
    return STATIC_ROUTE_DISTANCE_BY_VENDOR.get(str(vendor or ""), STATIC_ROUTE_DISTANCE)


def negotiated(value: str | None) -> str | None:
    """速度・二重の値。`auto` と書いたものは、書いていないのと同じ（既定が自動）。

    固定していれば値を、自動なら None を返す。
    出典: Cisco「Cisco IOS XE 17 - Interface Characteristics Configuration Guide」の Default
    Ethernet interface configuration（Speed: Autonegotiate、Duplex mode: Autonegotiate）
    """
    if value is None or value.strip().lower() == "auto":
        return None
    return value


def ospf_auth(value: str | None) -> str | None:
    """OSPF の口の認証。掛けていなければ None。

    書いていない（None）のと、`ip ospf authentication null`（"none"）は同じ「掛けていない」。
    出典: Cisco「Configure Authentication in Open Shortest Path First」（Null Authentication は
    Type 0 で既定）、Nexus 6000 Series NX-OS Unicast Routing Command Reference の
    ip ospf authentication（null: no authentication is used）
    """
    return None if value in (None, "none") else value
