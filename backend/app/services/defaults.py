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
#: 未確認: 他社の既定（Huawei の preference、Junos の preference、FortiGate の distance）は
#: 資料に当たっていないので、同じ値として扱っている
STATIC_ROUTE_DISTANCE = 1


def fhrp_priority(entry: _Redundancy, ports: list[_Port]) -> int:
    """実際に効く HSRP・VRRP の優先度。書いていなければ 100、VRRP の持ち主は 255。"""
    if entry.protocol == "vrrp" and entry.virtual_ip:
        port = next((p for p in ports if p.name == entry.interface), None)
        addresses = [port.ip_address, *port.extra_addresses] if port else []
        if any(a and a.split("/")[0] == entry.virtual_ip for a in addresses):
            return VRRP_OWNER_PRIORITY
    return entry.priority if entry.priority is not None else FHRP_PRIORITY


def static_distance(distance: int | None) -> int:
    """実際に効く静的経路の距離。"""
    return distance if distance is not None else STATIC_ROUTE_DISTANCE


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
