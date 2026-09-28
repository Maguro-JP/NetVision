"""HPE / Aruba の AOS-S（ProCurve 2530 / 2930 など）の config を読む。

同じ Aruba でも AOS-CX とは別物である。書き方は FastIron に近く、
VLAN の下に口を並べる。

- `vlan 10` の下に `name "USERS"` と `untagged 1-20` / `tagged 24` が並ぶ。
  口の下に VLAN を書く IOS とは向きが逆
- 口は番号だけ（`interface 24`）。名前は `name "..."`
- VLAN の住所は VLAN の下（`ip address 10.0.0.5 255.255.255.0`）
- 束ねるのは `trunk 23-24 trk1 lacp`
- 名前は引用符で囲う

タグ付きと一緒に載っているタグ無しは、その口のタグ無し VLAN（native）である。
アクセス VLAN として持つと、両端で食い違っていても比べられない。

出典: HPE Aruba「VLAN and Link-Aggregation Interoperability ArubaOS-Switch and
Cisco IOS」の作例（`vlan 10` の下の `untagged a1`・`tagged a1`、
`trunk a1-a2 trk1 lacp`）。口の側に書く `tagged vlan 20-22,25` の形は
HPE Aruba の Airheads コミュニティの作例で確かめた。どちらも表番号は未確認。
スタックの `1/1-1/48` の範囲の書き方は作例の形に合わせた（未確認）。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    ParsedConfig,
    ParsedInterface,
    ParsedRoute,
    Vendor,
)
from app.services.configparse.vlanlist import parse_ranges

#: AOS-S にしか出てこない書き方。1つでも当たれば AOS-S とみなす
_STRONG = (
    re.compile(r"^\s*module\s+\d+\s+type\s+\S+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*trunk\s+[\w\-,/]+\s+trk\d+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*(?:un)?tagged\s+[\d\-,/]+\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bProCurve\b|\bJL\d{3}[A-Z]\b|\bJ9\d{3}[A-Z]\b", re.IGNORECASE),
)
#: `ip default-gateway` は Cisco にもある。これを目印にすると IOS を横取りする
#: AOS-S だけの目印にはならないが、これが無いと AOS-S とは言えないもの
_WEAK = re.compile(r"^\s*vlan\s+\d+\s*$", re.IGNORECASE | re.MULTILINE)

_HOSTNAME = re.compile(r'^\s*hostname\s+"?([^"\s]+)"?', re.IGNORECASE | re.MULTILINE)
_MODULE = re.compile(r"^\s*module\s+\d+\s+type\s+(\S+)", re.IGNORECASE | re.MULTILINE)
#: 先頭の注記に版が入る。`; JL253A Configuration Editor; Created on release #YA.16.10.0013`
_VERSION = re.compile(r"^\s*;.*Created on release\s+#?(\S+)", re.IGNORECASE | re.MULTILINE)
_SHOW_VERSION = re.compile(r"^\s*Software revision\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SERIAL = re.compile(r"^\s*Serial Number\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)

_VLAN_HEAD = re.compile(r"^vlan\s+(\d+)\s*$", re.IGNORECASE)
_VLAN_NAME = re.compile(r'^\s+name\s+"?(.+?)"?\s*$', re.IGNORECASE)
_MEMBERS = re.compile(
    r"^\s+(?P<how>tagged|untagged)\s+(?P<ports>[\d\-,/A-Za-z]+)\s*$", re.IGNORECASE
)
_VLAN_IP = re.compile(
    r"^\s+ip\s+address\s+(?P<ip>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>\d+\.\d+\.\d+\.\d+)",
    re.IGNORECASE,
)
_INTERFACE = re.compile(r"^interface\s+(\S+)\s*$", re.IGNORECASE)
_NAME = re.compile(r'^\s+name\s+"?(.+?)"?\s*$', re.IGNORECASE)
_DISABLE = re.compile(r"^\s+disable\s*$", re.IGNORECASE)
_TRUNK = re.compile(r"^\s*trunk\s+(?P<ports>[\w\-,/]+)\s+(?P<name>trk\d+)", re.IGNORECASE)
_GATEWAY = re.compile(r"^\s*ip\s+default-gateway\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_ROUTE = re.compile(
    r"^\s*ip\s+route\s+(?P<dest>\d+\.\d+\.\d+\.\d+)\s+(?P<mask>\d+\.\d+\.\d+\.\d+)\s+(?P<hop>\S+)",
    re.IGNORECASE | re.MULTILINE,
)


def detect(text: str) -> Vendor | None:
    """AOS-S かどうか。AOS-CX や FastIron と取り違えないだけの目印で見る。"""
    for pattern in _STRONG:
        if pattern.search(text):
            # FastIron も VLAN の下に口を並べるが、`by port` と `port-name` が付く。
            # そちらが出ているなら、こちらの config ではない
            if re.search(r"\bby\s+port\b|\bport-name\b", text, re.IGNORECASE):
                return None
            if _WEAK.search(text) or re.search(r"\bProCurve\b|\bJL\d", text, re.IGNORECASE):
                return Vendor.ARUBA_OS
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.ARUBA_OS, os_name="ArubaOS-Switch")

    found = _HOSTNAME.search(text)
    if found:
        parsed.hostname = found.group(1)
    found = _MODULE.search(text)
    if found:
        parsed.model = found.group(1)
    found = _VERSION.search(text) or _SHOW_VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _SERIAL.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _read_ports(text, parsed)
    _read_vlans(text, parsed)
    _read_port_vlans(text, parsed)
    _read_trunks(text, parsed)
    _read_routes(text, parsed)
    return parsed


def _read_ports(text: str, parsed: ParsedConfig) -> None:
    """口。名前と、落としてあるかどうか。"""
    current: ParsedInterface | None = None
    for raw in text.splitlines():
        head = _INTERFACE.match(raw)
        if head and not raw.startswith((" ", "\t")):
            current = parsed.add_interface(ParsedInterface(name=_port_name(head.group(1))))
            continue
        if current is None:
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            current = None
            continue
        name = _NAME.match(raw)
        if name:
            current.description = name.group(1)
            continue
        if _DISABLE.match(raw):
            # AOS-S は既定で上がっている。落としてあるときだけ書く
            current.enabled = False


def _read_vlans(text: str, parsed: ParsedConfig) -> None:
    """VLAN と、その下に並ぶ口。向きが IOS と逆なので、ここで組み直す。"""
    current: int | None = None
    untagged: dict[str, int] = {}
    for raw in text.splitlines():
        head = _VLAN_HEAD.match(raw)
        if head and not raw.startswith((" ", "\t")):
            current = int(head.group(1))
            parsed.vlans.setdefault(current, "")
            continue
        if current is None:
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            current = None
            continue

        name = _VLAN_NAME.match(raw)
        if name:
            parsed.vlans[current] = name.group(1)
            continue
        address = _VLAN_IP.match(raw)
        if address:
            port = parsed.add_interface(ParsedInterface(name=f"Vlan{current}"))
            port.ip_address = f"{address.group('ip')}/{_prefixlen(address.group('mask'))}"
            port.vlan = current
            continue
        members = _MEMBERS.match(raw)
        if not members:
            continue
        tagged = members.group("how").lower() == "tagged"
        for name in _ports(members.group("ports")):
            port = parsed.add_interface(ParsedInterface(name=name))
            if tagged:
                port.mode = "trunk"
                if current not in port.trunk_vlans:
                    port.trunk_vlans.append(current)
            else:
                untagged.setdefault(name, current)

    # タグ付きと一緒に載っているタグ無しは、その口のタグ無し VLAN（native）。
    # 読み終えてから決める（タグ無しの行が先に来ることがある）
    for name, vlan_id in untagged.items():
        port = parsed.add_interface(ParsedInterface(name=name))
        if port.trunk_vlans:
            port.mode = "trunk"
            port.native_vlan = port.native_vlan or vlan_id
        else:
            port.mode = port.mode or "access"
            port.vlan = port.vlan or vlan_id


#: 口の側に書く形（`interface Trk1` の下の `tagged vlan 20-22,25` / `untagged vlan 30`）
_PORT_VLANS = re.compile(
    r"^\s+(?P<how>tagged|untagged)\s+vlan\s+(?P<vlans>[\d\-,]+)\s*$", re.IGNORECASE
)


def _read_port_vlans(text: str, parsed: ParsedConfig) -> None:
    """口の側に書いた VLAN。VLAN の下に並べる形と同じ意味で、入力の向きが違うだけ。

    `trunk 1/49,1/50 trk1 lacp` のあと `interface trk1` の下で
    `tagged vlan 10-20,25`・`untagged vlan 30` と書く形（HPE Aruba の Airheads と
    AOS-S / Cisco 相互運用資料の作例）。
    """
    current: ParsedInterface | None = None
    untagged: dict[str, int] = {}
    for raw in text.splitlines():
        head = _INTERFACE.match(raw)
        if head and not raw.startswith((" ", "\t")):
            current = parsed.add_interface(ParsedInterface(name=_port_name(head.group(1))))
            continue
        if current is None:
            continue
        if raw.strip() and not raw.startswith((" ", "\t")):
            current = None
            continue
        found = _PORT_VLANS.match(raw)
        if not found:
            continue
        vlans = parse_ranges(found.group("vlans"))
        if found.group("how").lower() == "tagged":
            current.mode = "trunk"
            current.trunk_vlans.extend(v for v in vlans if v not in current.trunk_vlans)
        elif vlans:
            untagged[current.name] = vlans[0]

    for name, vlan_id in untagged.items():
        port = parsed.add_interface(ParsedInterface(name=name))
        if port.trunk_vlans:
            port.native_vlan = vlan_id
        else:
            port.mode = port.mode or "access"
            port.vlan = vlan_id


def _read_trunks(text: str, parsed: ParsedConfig) -> None:
    """束ね。`trunk 23-24 trk1 lacp` の形で、まとめて1行に書く。"""
    for raw in text.splitlines():
        found = _TRUNK.match(raw)
        if not found:
            continue
        group = int(re.sub(r"\D", "", found.group("name")) or 0)
        for name in _ports(found.group("ports")):
            port = parsed.add_interface(ParsedInterface(name=name))
            port.lag_group = group
            port.lag_mode = "active" if "lacp" in raw.lower() else "on"


def _read_routes(text: str, parsed: ParsedConfig) -> None:
    for found in _ROUTE.finditer(text):
        parsed.routes.append(
            ParsedRoute(
                destination=f"{found.group('dest')}/{_prefixlen(found.group('mask'))}",
                next_hop=found.group("hop"),
            )
        )
    found = _GATEWAY.search(text)
    if found and not any(route.destination == "0.0.0.0/0" for route in parsed.routes):
        # 既定の経路をこの語で書く。`ip route` と同じ意味なので、同じ形で持つ
        parsed.routes.append(
            ParsedRoute(
                destination="0.0.0.0/0",
                next_hop=found.group(1),
            )
        )


def _ports(written: str) -> list[str]:
    """`1-4,24` のような並びをほどく。"""
    names: list[str] = []
    for part in written.split(","):
        part = part.strip()
        if not part:
            continue
        # `1-4`、スタックの `1/1-1/48`、モジュールの `A1-A4`。前置き（`1/`・`A`）が
        # 両端で同じときだけ番号を開く。前置きの違う範囲は機器にしか分からない
        found = re.fullmatch(
            r"(?P<head>[A-Za-z]?(?:\d+/)*[A-Za-z]?)(?P<start>\d+)"
            r"-(?P=head)(?P<end>\d+)",
            part,
        )
        if found:
            start, end = int(found.group("start")), int(found.group("end"))
            if start <= end:
                head = found.group("head")
                names.extend(f"{head}{number}" for number in range(start, end + 1))
            continue
        names.append(part)
    return [_port_name(name) for name in names]


def _port_name(name: str) -> str:
    """番号だけの口に、他ベンダーと並べても分かる名前を付ける。

    `24` のままだと、台帳で別の機器の口と見分けが付かない。
    """
    name = name.strip()
    if re.fullmatch(r"\d+", name):
        return f"Port{name}"
    if re.fullmatch(r"[Tt]rk\d+", name):
        return name.capitalize()
    return name


def _prefixlen(mask: str) -> int:
    return sum(bin(int(part)).count("1") for part in mask.split("."))
