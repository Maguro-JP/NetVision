"""アライドテレシス AlliedWare Plus（x230 / x530 など）の config を読む。

書き方は IOS にかなり近く、`switchport` まわりはそのまま読める。違うのは
次の3つで、そこだけこちらで読み直す。

- ポート名が `port1.0.1` / `vlan10` / `po1`（束）の形
- アドレスと経路が長さ付き（`ip address 10.0.10.1/24`）
- VLAN の名前は `vlan database` の下に `vlan 10 name USERS` と1行で並ぶ。
  IOS のように `vlan 10` の下の `name` ではないので、そのままだと名前が入らない
"""

from __future__ import annotations

import re

from app.services.configparse import cisco
from app.services.configparse.base import ParsedConfig, ParsedRoute, Vendor
from app.services.configparse.routing import to_prefix

_VERSION = re.compile(r"AlliedWare Plus.*?(?:version|Version)\s+([\d.\-A-Za-z]+)", re.IGNORECASE)
_BUILD = re.compile(r"^!?\s*Build name\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_MODEL = re.compile(r"^!?\s*(?:Base|Product)\s+model\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SERIAL = re.compile(r"^!?\s*Serial number\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_VLAN_LINE = re.compile(r"^vlan\s+(\d+)\s+name\s+(\S+)", re.IGNORECASE)
_IP_ADDRESS = re.compile(r"^ip address\s+(\d+\.\d+\.\d+\.\d+/\d+)", re.IGNORECASE)
_INTERFACE = re.compile(r"^interface\s+(\S+)", re.IGNORECASE)
_ROUTE = re.compile(
    r"^ip route\s+(?:vrf\s+(?P<vrf>\S+)\s+)?(?P<dest>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)


def detect(text: str) -> Vendor | None:
    """AlliedWare Plus かどうか。IOS と見分けが付く手掛かりだけで見る。"""
    if re.search(r"AlliedWare Plus", text, re.IGNORECASE):
        return Vendor.ALLIED
    for pattern in (
        r"^interface\s+port\d+\.\d+\.\d+",
        r"^vlan database\s*$",
        r"^\s*Allied Telesis",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.ALLIED
    return None


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、AlliedWare Plus の書き方で上から直す。"""
    parsed = cisco.parse(text, Vendor.CISCO_IOS)
    parsed.vendor = Vendor.ALLIED
    parsed.os_name = "AlliedWare Plus"
    parsed.os_version = None
    parsed.model = None

    found = _VERSION.search(text) or _BUILD.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    found = _SERIAL.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _parse_vlan_database(text, parsed)
    _parse_addresses(text, parsed)
    _parse_routes(text, parsed)
    return parsed


def _parse_vlan_database(text: str, parsed: ParsedConfig) -> None:
    """`vlan database` の下の `vlan 10 name USERS`。

    IOS の読み手は `vlan 10` を定義として拾うが、名前は次の行の `name` を
    見にいくので入らない。1行で書かれたものをここで入れる。
    """
    for raw in text.splitlines():
        found = _VLAN_LINE.match(raw.strip())
        if not found:
            continue
        number = int(found.group(1))
        parsed.vlans[number] = found.group(2)


def _parse_addresses(text: str, parsed: ParsedConfig) -> None:
    """`ip address 10.0.10.1/24`。IOS の読み手はマスク付きしか読めない。"""
    ports = {interface.key: interface for interface in parsed.interfaces}
    current = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        match = _INTERFACE.match(line.strip())
        if match and not line.startswith(" "):
            from app.services.configparse.ifname import normalize

            current = ports.get(normalize(match.group(1)))
            continue
        if current is None or not line.startswith(" "):
            continue
        found = _IP_ADDRESS.match(line.strip())
        if found:
            current.ip_address = found.group(1)


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    """`ip route 0.0.0.0/0 10.0.10.254`。長さ付きなので IOS の読み手は拾えない。"""
    seen = {(route.destination, route.next_hop) for route in parsed.routes}
    for raw in text.splitlines():
        found = _ROUTE.match(raw.strip())
        if not found:
            continue
        destination = to_prefix(found.group("dest"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination, vrf=found.group("vrf"))
        for token in found.group("rest").split():
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^(port|vlan|po|sa)", token, re.IGNORECASE):
                route.interface = token
        if (route.destination, route.next_hop) in seen:
            continue
        seen.add((route.destination, route.next_hop))
        parsed.routes.append(route)
