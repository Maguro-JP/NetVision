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
#:   まだ読んでいない
#: 未確認: NEC QX（H3C の OEM だが資料に届かない）。
#: `ip route-static default-preference` で既定を変えた機器は、まだ読んでいない
STATIC_ROUTE_DISTANCE_BY_VENDOR: dict[str, int] = {
    "huawei": 60,
    "h3c": 60,
    "fortinet": 10,
    "juniper": 5,
}


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
