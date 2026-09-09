"""Cisco IOS-XR の config を読む。

同じ Cisco でも IOS とは書き方が違うところがある。そこを直さないと、
IOS の読み手が「読めた」形で通してしまい、抜けたことに気づけない。

- 住所は `ipv4 address 10.0.0.1 255.255.255.0`（IOS は `ip address`）。
  ここを読み落とすと、どの口にも住所が無いことになる
- 静的経路は `router static` の下、`address-family ipv4 unicast` の中に
  `宛先/長さ ネクストホップ` だけを並べる。`ip route` という語が出てこない
- OSPF は `router ospf 1` の下に `area 0` の囲みがあり、その中に口を並べる
- 束ねるのは `Bundle-Ether1`
- VRF は `vrf NAME` の囲み（IOS の `vrf definition`）、口には `vrf NAME`
- BGP は相手ごとに `neighbor 10.0.0.1` の囲みを作り、その中に `remote-as` や
  `address-family` を並べる（IOS は `neighbor 10.0.0.1 remote-as 65000` と1行）。
  VRF ごとの部分も `vrf NAME` の囲みで、`rd` はそこに書く

VLAN や ACL は IOS の読み手でそのまま読めるので、上の分だけ直す。
"""

from __future__ import annotations

import ipaddress
import re

from app.services.configparse import cisco, routing
from app.services.configparse.base import (
    ParsedBgp,
    ParsedConfig,
    ParsedOspfArea,
    ParsedRoute,
    ParsedVrf,
    Vendor,
)

#: IOS-XR にしか出てこない書き方
_STRONG = (
    re.compile(r"^!!\s*IOS\s*XR\s+Configuration", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^RP/\d+/RP\d+/CPU\d+:", re.MULTILINE),
    # IOS は `ip address`。`ipv4 address` は XR の書き方
    re.compile(r"^\s+ipv4\s+address\s+\d", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^router\s+static\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^interface\s+Bundle-Ether\d", re.IGNORECASE | re.MULTILINE),
)

_VERSION = re.compile(r"^!!\s*IOS\s*XR\s+Configuration\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_SHOW_VERSION = re.compile(r"Cisco IOS XR Software.*?Version\s+(\S+)", re.IGNORECASE | re.DOTALL)
_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_IPV4 = re.compile(
    r"^\s+ipv4\s+address\s+(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>\d+\.\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_IPV4_LEN = re.compile(
    r"^\s+ipv4\s+address\s+(?P<ip>\d+\.\d+\.\d+\.\d+)/(?P<len>\d+)", re.IGNORECASE
)
_IPV4_DHCP = re.compile(r"^\s+ipv4\s+address\s+dhcp\s*$", re.IGNORECASE)
#: `0.0.0.0/0 10.0.0.254` / `10.1.0.0/16 GigabitEthernet0/0/0/0 10.0.0.254`
_STATIC = re.compile(
    r"^\s+(?P<dest>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<rest>.+?)\s*$",
)
_OSPF_HEAD = re.compile(r"^router\s+ospf\s+(\S+)\s*$", re.IGNORECASE)
_AREA = re.compile(r"^\s+area\s+(\S+)\s*$", re.IGNORECASE)
_AREA_INTERFACE = re.compile(r"^\s+interface\s+(\S+)\s*$", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    for pattern in _STRONG:
        if pattern.search(text):
            return Vendor.CISCO_XR
    return None


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、XR の書き方で足りない分を入れる。"""
    parsed = cisco.parse(text, Vendor.CISCO_IOS)
    parsed.vendor = Vendor.CISCO_XR
    parsed.os_name = "Cisco IOS XR"

    found = _VERSION.search(text) or _SHOW_VERSION.search(text)
    parsed.os_version = found.group(1) if found else parsed.os_version

    _read_addresses(text, parsed)
    _read_vrfs(text, parsed)
    _read_static(text, parsed)
    _read_ospf_areas(text, parsed)
    _read_bgp(text, parsed)
    return parsed


_VRF_HEAD = re.compile(r"^vrf\s+(\S+)\s*$", re.IGNORECASE)
_INTERFACE_VRF = re.compile(r"^\s+vrf\s+(\S+)\s*$", re.IGNORECASE)


def _read_vrfs(text: str, parsed: ParsedConfig) -> None:
    """`vrf NAME` の囲みと、口の `vrf NAME`。IOS の読み手は `vrf definition` しか見ない。"""
    by_name = {port.name: port for port in parsed.interfaces}
    current = None
    for raw in text.splitlines():
        if raw.strip() and not raw.startswith((" ", "\t")):
            head = _VRF_HEAD.match(raw)
            if head:
                parsed.add_vrf(ParsedVrf(name=head.group(1)))
            port = _INTERFACE.match(raw)
            current = by_name.get(port.group(1)) if port else None
            continue
        if current is None:
            continue
        found = _INTERFACE_VRF.match(raw)
        if found and current.vrf is None:
            current.vrf = found.group(1)


_BGP_HEAD = re.compile(r"^router\s+bgp\s+(\S+)\s*$", re.IGNORECASE)


def _read_bgp(text: str, parsed: ParsedConfig) -> None:
    """`router bgp` の中の、相手ごとの囲み。

    行の深さ（字下げ）で、どの囲みの中にいるかを判断する。
    出典: BGP Configuration Guide for Cisco NCS 5500「Implementing BGP」の
    running configuration 例（neighbor → remote-as → address-family → route-policy … in）。
    `neighbor 10.0.0.2` の下の `remote-as` はその相手のもの、`vrf CUST-A` の
    下の `neighbor` はその VRF の相手、`address-family` は直上の相手の家族。
    """
    lines = text.splitlines()
    starts = [i for i, raw in enumerate(lines) if _BGP_HEAD.match(raw)]
    if not starts:
        return
    bgp = parsed.bgp or ParsedBgp()
    bgp.local_as = bgp.local_as or _BGP_HEAD.match(lines[starts[0]]).group(1)
    # XR は相手の上げ下げを既定で記録する。止めるときだけ
    # `bgp log neighbor changes disable` と書く（IOS は入れないと記録しない）
    # （出典: BGP Command Reference for Cisco NCS 5500「bgp log neighbor changes disable」）
    bgp.log_neighbor_changes = not re.search(
        r"^\s+bgp log neighbor changes disable\s*$", text, re.IGNORECASE | re.MULTILINE
    )

    for start in starts:
        neighbor: str | None = None
        neighbor_depth = 0
        vrf: str | None = None
        vrf_depth = 0
        for raw in lines[start + 1 :]:
            stripped = raw.strip()
            if not stripped or stripped == "!":
                continue
            depth = len(raw) - len(raw.lstrip())
            if depth == 0:
                break
            if neighbor is not None and depth <= neighbor_depth:
                neighbor = None
            if vrf is not None and depth <= vrf_depth:
                vrf = None
                if depth <= vrf_depth:
                    neighbor = None

            router_id = re.match(r"^bgp router-id\s+(\S+)", stripped, re.IGNORECASE)
            if router_id and vrf is None:
                bgp.router_id = router_id.group(1)
                continue
            head = re.match(r"^vrf\s+(\S+)\s*$", stripped, re.IGNORECASE)
            if head:
                vrf, vrf_depth = head.group(1), depth
                continue
            rd = re.match(r"^rd\s+(\S+)", stripped, re.IGNORECASE)
            if rd and vrf is not None:
                found = parsed.add_vrf(ParsedVrf(name=vrf))
                found.rd = found.rd or rd.group(1)
                continue
            head = re.match(r"^neighbor\s+(\S+)\s*$", stripped, re.IGNORECASE)
            if head:
                neighbor, neighbor_depth = head.group(1), depth
                routing._add_neighbor(bgp, neighbor, "")
                continue
            if neighbor is None:
                continue
            family = re.match(r"^address-family\s+(.+)$", stripped, re.IGNORECASE)
            if family:
                routing._add_neighbor(bgp, neighbor, "activate", family.group(1).strip().lower())
                continue
            # XR は route-policy を in / out に当てる（IOS の route-map と同じ役目）
            policy = re.match(r"^route-policy\s+(\S+)\s+(in|out)\b", stripped, re.IGNORECASE)
            if policy:
                routing._add_neighbor(
                    bgp, neighbor, f"route-map {policy.group(1)} {policy.group(2)}"
                )
                continue
            routing._add_neighbor(bgp, neighbor, stripped)

    parsed.bgp = bgp


def _read_addresses(text: str, parsed: ParsedConfig) -> None:
    """`ipv4 address`。IOS の読み手は `ip address` しか見ていない。"""
    by_name = {port.name: port for port in parsed.interfaces}
    current = None
    for raw in text.splitlines():
        head = _INTERFACE.match(raw)
        if head and not raw.startswith((" ", "\t")):
            current = by_name.get(head.group(1))
            continue
        if current is None:
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            current = None
            continue

        if _IPV4_DHCP.match(raw):
            # 住所が無いのではなく、貰う設定。空欄にすると付け忘れと見分けが付かない
            current.address_source = "dhcp"
            continue
        found = _IPV4_LEN.match(raw)
        if found:
            current.ip_address = f"{found.group('ip')}/{found.group('len')}"
            continue
        found = _IPV4.match(raw)
        if found:
            current.ip_address = f"{found.group('ip')}/{_prefixlen(found.group('mask'))}"


def _read_static(text: str, parsed: ParsedConfig) -> None:
    """`router static` の中。`ip route` という語が出てこないので、囲みで読む。"""
    inside = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if not raw.startswith((" ", "\t")) and stripped:
            inside = bool(re.match(r"^router\s+static\s*$", stripped, re.IGNORECASE))
            continue
        if not inside or not stripped or stripped == "!":
            continue
        found = _STATIC.match(raw)
        if not found:
            continue
        parts = found.group("rest").split()
        # 次に渡す先は、住所か口の名前か、その両方が書かれる
        hop = next((part for part in parts if _is_address(part)), None)
        via = next((part for part in parts if not _is_address(part) and not part.isdigit()), None)
        parsed.routes.append(
            ParsedRoute(destination=found.group("dest"), next_hop=hop, interface=via)
        )


def _read_ospf_areas(text: str, parsed: ParsedConfig) -> None:
    """OSPF の area は囲みで、その中に口を並べる。IOS の `network` 行が無い。"""
    if parsed.ospf is None:
        return
    areas: dict[str, list[str]] = {}
    in_ospf = False
    current: str | None = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if not raw.startswith((" ", "\t")) and stripped:
            in_ospf = bool(_OSPF_HEAD.match(stripped))
            current = None
            continue
        if not in_ospf:
            continue
        found = _AREA.match(raw)
        if found:
            current = found.group(1)
            areas.setdefault(current, [])
            continue
        if current is None:
            continue
        member = _AREA_INTERFACE.match(raw)
        if member:
            areas[current].append(member.group(1))

    for name, ports in areas.items():
        # XR は area の中に口を並べる。IOS の network 行に当たるものが無いので、
        # 口の名前をそのまま入れておく（どのエリアに何が入っているかは残る）
        parsed.ospf.areas.append(ParsedOspfArea(area=name, networks=ports))


def _is_address(value: str) -> bool:
    try:
        ipaddress.IPv4Address(value)
    except ValueError:
        return False
    return True


def _prefixlen(mask: str) -> int:
    return sum(bin(int(part)).count("1") for part in mask.split("."))
