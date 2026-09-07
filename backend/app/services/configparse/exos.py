"""Extreme Networks EXOS の config を読む。

書き方が他社とまるで違う。`interface` という囲みが無く、`configure` で
始まる1行ずつの命令が並ぶ。IOS の目で読むと、口も VLAN も1つも取れない。

- VLAN は `create vlan "USERS" tag 10`
- 口は VLAN に足す形（`configure vlan USERS add ports 1-20 untagged`）。
  向きは FastIron や AOS-S と同じで、VLAN の側から口を並べる
- 住所は `configure vlan MGMT ipaddress 10.0.0.5/24`
- 名前は `configure ports 1 display-string "..."`
- 束ねるのは `enable sharing 23 grouping 23-24 algorithm ... lacp`
- 落とすのは `disable ports 21`

タグ付きと一緒に載っているタグ無しは、その口のタグ無し VLAN（native）。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    ParsedConfig,
    ParsedInterface,
    ParsedRoute,
    Vendor,
)

#: EXOS にしか出てこない書き方
_STRONG = (
    re.compile(r"^\s*create\s+vlan\s+", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*configure\s+vlan\s+\S+\s+add\s+ports\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*configure\s+snmp\s+sysName\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*enable\s+sharing\s+\S+\s+grouping\s", re.IGNORECASE | re.MULTILINE),
    re.compile(r"\bExtremeXOS\b", re.IGNORECASE),
)

_HOSTNAME = re.compile(r'^\s*configure\s+snmp\s+sysName\s+"?([^"\n]+?)"?\s*$', re.I | re.M)
_VERSION = re.compile(r"ExtremeXOS(?:\s+version)?\s+([\d.]+[\w.-]*)", re.IGNORECASE)
_MODEL = re.compile(r"^\s*#\s*Switch\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_CREATE_VLAN = re.compile(
    r'^\s*create\s+vlan\s+"?(?P<name>[^"\s]+)"?(?:\s+tag\s+(?P<tag>\d+))?', re.IGNORECASE
)
_VLAN_TAG = re.compile(r'^\s*configure\s+vlan\s+"?(?P<name>[^"\s]+)"?\s+tag\s+(?P<tag>\d+)', re.I)
_ADD_PORTS = re.compile(
    r'^\s*configure\s+vlan\s+"?(?P<name>[^"\s]+)"?\s+add\s+ports?\s+(?P<ports>[\d\-,:]+)'
    r"(?:\s+(?P<how>tagged|untagged))?",
    re.IGNORECASE,
)
_VLAN_IP = re.compile(
    r'^\s*configure\s+vlan\s+"?(?P<name>[^"\s]+)"?\s+ipaddress\s+'
    r"(?P<ip>\d+\.\d+\.\d+\.\d+)[\s/](?P<mask>[\d.]+)",
    re.IGNORECASE,
)
_DISPLAY = re.compile(
    r'^\s*configure\s+ports?\s+(?P<ports>[\d\-,:]+)\s+display-string\s+"?(?P<name>[^"\n]+?)"?\s*$',
    re.IGNORECASE,
)
_DISABLE = re.compile(r"^\s*disable\s+ports?\s+(?P<ports>[\d\-,:]+)", re.IGNORECASE)
_SHARING = re.compile(
    r"^\s*enable\s+sharing\s+(?P<master>[\d:]+)\s+grouping\s+(?P<ports>[\d\-,:]+)", re.IGNORECASE
)
_ROUTE = re.compile(
    r"^\s*configure\s+iproute\s+add\s+(?:(?P<default>default)|"
    r"(?P<dest>\d+\.\d+\.\d+\.\d+)[\s/](?P<mask>[\d.]+))\s+(?P<hop>\d+\.\d+\.\d+\.\d+)",
    re.IGNORECASE,
)


def detect(text: str) -> Vendor | None:
    for pattern in _STRONG:
        if pattern.search(text):
            return Vendor.EXOS
    return None


def parse(text: str) -> ParsedConfig:
    parsed = ParsedConfig(vendor=Vendor.EXOS, os_name="ExtremeXOS")

    found = _HOSTNAME.search(text)
    if found:
        parsed.hostname = found.group(1).strip()
    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)

    #: VLAN の名前から番号を引く。口は名前で足されるので、これが無いと繋がらない
    tags: dict[str, int] = {}
    untagged: dict[str, int] = {}

    for raw in text.splitlines():
        create = _CREATE_VLAN.match(raw)
        if create:
            name = create.group("name")
            if create.group("tag"):
                number = int(create.group("tag"))
                tags[name] = number
                parsed.vlans[number] = name
            continue
        tag = _VLAN_TAG.match(raw)
        if tag:
            number = int(tag.group("tag"))
            tags[tag.group("name")] = number
            parsed.vlans[number] = tag.group("name")
            continue

    for raw in text.splitlines():
        display = _DISPLAY.match(raw)
        if display:
            for name in _ports(display.group("ports")):
                parsed.add_interface(ParsedInterface(name=name)).description = display.group("name")
            continue
        disabled = _DISABLE.match(raw)
        if disabled:
            for name in _ports(disabled.group("ports")):
                # EXOS は既定で上がっている。落としてあるときだけ書く
                parsed.add_interface(ParsedInterface(name=name)).enabled = False
            continue
        sharing = _SHARING.match(raw)
        if sharing:
            group = _group_number(sharing.group("master"))
            for name in _ports(sharing.group("ports")):
                port = parsed.add_interface(ParsedInterface(name=name))
                port.lag_group = group
                port.lag_mode = "active" if "lacp" in raw.lower() else "on"
            continue
        address = _VLAN_IP.match(raw)
        if address:
            number = tags.get(address.group("name"))
            if number is None:
                continue
            port = parsed.add_interface(ParsedInterface(name=f"Vlan{number}"))
            port.vlan = number
            port.ip_address = f"{address.group('ip')}/{_prefixlen(address.group('mask'))}"
            continue
        members = _ADD_PORTS.match(raw)
        if members:
            number = tags.get(members.group("name"))
            if number is None:
                continue
            # 書いていなければタグ無し。EXOS の既定はそちら
            tagged = (members.group("how") or "untagged").lower() == "tagged"
            for name in _ports(members.group("ports")):
                port = parsed.add_interface(ParsedInterface(name=name))
                if tagged:
                    port.mode = "trunk"
                    if number not in port.trunk_vlans:
                        port.trunk_vlans.append(number)
                else:
                    untagged.setdefault(name, number)
            continue
        route = _ROUTE.match(raw)
        if route:
            if route.group("default"):
                destination = "0.0.0.0/0"
            else:
                destination = f"{route.group('dest')}/{_prefixlen(route.group('mask'))}"
            parsed.routes.append(ParsedRoute(destination=destination, next_hop=route.group("hop")))

    # タグ付きと一緒に載っているタグ無しは、その口のタグ無し VLAN（native）。
    # アクセス VLAN として持つと、両端で食い違っていても比べられない
    for name, number in untagged.items():
        port = parsed.add_interface(ParsedInterface(name=name))
        if port.trunk_vlans:
            port.mode = "trunk"
            port.native_vlan = port.native_vlan or number
        else:
            port.mode = port.mode or "access"
            port.vlan = port.vlan or number
    return parsed


def _ports(written: str) -> list[str]:
    """`1-4,24` や `1:1-1:4` をほどく。"""
    names: list[str] = []
    for part in written.split(","):
        part = part.strip()
        if not part:
            continue
        found = re.fullmatch(r"(\d+)-(\d+)", part)
        if found:
            start, end = int(found.group(1)), int(found.group(2))
            if start <= end:
                names.extend(str(number) for number in range(start, end + 1))
            continue
        # 積んである機器は `1:24` の形（何台目の何番か）。範囲は台が同じときだけ
        stacked = re.fullmatch(r"(\d+):(\d+)-(?:\d+:)?(\d+)", part)
        if stacked:
            unit, start, end = stacked.group(1), int(stacked.group(2)), int(stacked.group(3))
            if start <= end:
                names.extend(f"{unit}:{number}" for number in range(start, end + 1))
            continue
        names.append(part)
    return [_port_name(name) for name in names]


def _port_name(name: str) -> str:
    """番号だけの口に、台帳で見分けの付く名前を付ける。"""
    name = name.strip()
    return f"Port{name}" if re.fullmatch(r"[\d:]+", name) else name


def _group_number(master: str) -> int:
    return int(master.split(":")[-1])


def _prefixlen(mask: str) -> int:
    """`24` とも `255.255.255.0` とも書かれる。"""
    if "." not in mask:
        return int(mask)
    return sum(bin(int(part)).count("1") for part in mask.split("."))
