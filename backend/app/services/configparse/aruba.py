"""Aruba CX（AOS-CX）の config を読む。

Cisco IOS に似ているが、ポートの書き方がまるごと違う。

- 名前が `1/1/1` の形で、`switchport` を書かない。VLAN は `vlan access 20` /
  `vlan trunk allowed 10,20`
- アドレスは長さ付き（`ip address 10.0.10.1/24`）
- ポートは既定で落ちている。`no shutdown` を書いて初めて上がる。
  IOS と同じ目で見ると、全部のポートが「生きている」ことになってしまう
- 束ねるのは `lag 1` と `interface lag 1`
- ACL は `access-list ip 名前` の下に番号付きで並び、`apply access-list ip 名前 in`
  で当てる

VLAN や経路の道具、BGP / OSPF は IOS の読み手でそのまま読めるので、
ポートまわりと ACL、経路だけをこちらで見る。
"""

from __future__ import annotations

import re

from app.services.configparse import cisco
from app.services.configparse.base import (
    NeighborProtocol,
    ParsedAcl,
    ParsedAclEntry,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
    ParsedRoute,
    Vendor,
)
from app.services.configparse.routing import to_prefix

_VERSION = re.compile(r"^!\s*Version\s+ArubaOS-CX\s+(\S+)", re.IGNORECASE | re.MULTILINE)
_MODEL = re.compile(r"^!\s*Model:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_SERIAL = re.compile(r"^\s*Serial Number\s*:\s*(\S+)", re.IGNORECASE | re.MULTILINE)
_INTERFACE = re.compile(r"^interface\s+(\S+(?:\s+\d+)?)\s*$", re.IGNORECASE)
_ROUTE = re.compile(
    r"^ip route\s+(?P<dest>\d+\.\d+\.\d+\.\d+/\d+)\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_ACL_HEAD = re.compile(r"^access-list\s+ip\s+(\S+)\s*$", re.IGNORECASE)
_ACL_ENTRY = re.compile(r"^(?P<seq>\d+)\s+(?P<action>permit|deny)\s+(?P<rest>.+)$", re.IGNORECASE)
#: `show lldp neighbor-info` の見出し
_LLDP_HEADER = re.compile(
    r"^\s*LOCAL-PORT\s+CHASSIS-ID\s+PORT-ID\s+PORT-DESC\s+TTL\s+SYS-NAME\s*$", re.IGNORECASE
)
_APPLY = re.compile(r"^apply\s+access-list\s+ip\s+(\S+)\s+(in|out)\b", re.IGNORECASE)


def detect(text: str) -> Vendor | None:
    """AOS-CX かどうか。IOS と見分けが付く手掛かりだけで見る。"""
    if _VERSION.search(text):
        return Vendor.ARUBA_CX
    for pattern in (
        r"^\s*ArubaOS-CX",
        r"^\s*vlan trunk allowed\s",
        r"^\s*vlan access\s+\d+",
        r"^apply\s+access-list\s+ip\s",
        r"^vsx\s*$",
        r"^\s+(?:no\s+)?routing\s*$",
        r"^(?:ssh|https)-?server\s+vrf\s",
        r"^\s+vrrp\s+\d+\s+address-family\s",
    ):
        if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
            return Vendor.ARUBA_CX
    return None


def parse(text: str) -> ParsedConfig:
    """IOS の読み手に任せてから、AOS-CX の書き方で上から直す。"""
    parsed = cisco.parse(text, Vendor.CISCO_IOS)
    parsed.vendor = Vendor.ARUBA_CX
    parsed.os_name = "AOS-CX"
    parsed.os_version = None
    parsed.model = None
    # IOS の読み手が拾ったポートは名前しか当たっていない。中身はこちらで入れ直す
    parsed.interfaces = []
    parsed.acls = []

    found = _VERSION.search(text)
    if found:
        parsed.os_version = found.group(1)
    found = _MODEL.search(text)
    if found:
        parsed.model = found.group(1)
    found = _SERIAL.search(text)
    if found:
        parsed.serial_number = found.group(1)

    _parse_interfaces(text, parsed)
    _parse_routes(text, parsed)
    _parse_acls(text, parsed)
    _parse_lldp_table(text, parsed)
    return parsed


def _parse_interfaces(text: str, parsed: ParsedConfig) -> None:
    """ポート。既定で落ちている前提で読む。"""
    current: ParsedInterface | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue

        match = _INTERFACE.match(line.strip())
        if match and not line.startswith(" "):
            name = " ".join(match.group(1).split())
            # 書いていなければ落ちている。`no shutdown` を見たときだけ上げる
            current = parsed.add_interface(ParsedInterface(name=name, enabled=False))
            continue

        if current is None:
            continue
        if not line.startswith(" "):
            current = None
            continue

        _interface_option(current, line.strip())


def _interface_option(interface: ParsedInterface, body: str) -> None:
    if body.startswith("description "):
        interface.description = body[len("description ") :].strip()
    elif body.startswith("ip address "):
        value = body.split()[2]
        if value != "dhcp":
            interface.ip_address = value
        interface.mode = "routed"
    elif body.startswith("vlan access "):
        value = body.rsplit(maxsplit=1)[-1]
        if value.isdigit():
            interface.vlan = int(value)
        interface.mode = "access"
    elif body.startswith("vlan trunk native "):
        value = body.rsplit(maxsplit=1)[-1]
        if value.isdigit():
            interface.native_vlan = int(value)
        interface.mode = interface.mode or "trunk"
    elif body.startswith("vlan trunk allowed "):
        interface.mode = "trunk"
        rest = body[len("vlan trunk allowed ") :].strip()
        if rest.lower() == "all":
            interface.trunk_all = True
            interface.trunk_vlans = []
        else:
            interface.trunk_vlans = sorted(set(interface.trunk_vlans) | _vlan_list(rest))
            interface.trunk_all = False
    elif body.startswith("vrf attach "):
        interface.vrf = body.rsplit(maxsplit=1)[-1]
    elif body.startswith("mtu "):
        value = body.rsplit(maxsplit=1)[-1]
        if value.isdigit():
            interface.mtu = int(value)
    elif body.startswith("speed "):
        interface.speed = body.rsplit(maxsplit=1)[-1]
    elif body.startswith("lag "):
        value = body.rsplit(maxsplit=1)[-1]
        if value.isdigit():
            interface.lag_group = int(value)
    elif body.startswith("lacp mode "):
        interface.lag_mode = body.rsplit(maxsplit=1)[-1]
    elif body.startswith("ip ospf "):
        _ospf_option(interface, body)
    elif body == "no shutdown":
        interface.enabled = True
    elif body == "shutdown":
        interface.enabled = False
    elif body == "no routing":
        # 経路を切ると L2 のポートになる。VLAN の指定はこの後に来る
        interface.mode = interface.mode if interface.mode in ("access", "trunk") else None


def _ospf_option(interface: ParsedInterface, body: str) -> None:
    """`ip ospf 1 area 0.0.0.0` と、その下に続く細かい指定。"""
    parts = body.split()
    if "area" in parts:
        index = parts.index("area")
        if index + 1 < len(parts):
            interface.ospf_area = parts[index + 1]
    if "hello-interval" in parts:
        index = parts.index("hello-interval")
        if index + 1 < len(parts) and parts[index + 1].isdigit():
            interface.ospf_hello = int(parts[index + 1])
    if "dead-interval" in parts:
        index = parts.index("dead-interval")
        if index + 1 < len(parts) and parts[index + 1].isdigit():
            interface.ospf_dead = int(parts[index + 1])
    if "network" in parts:
        index = parts.index("network")
        if index + 1 < len(parts):
            interface.ospf_network = parts[index + 1]
    if "authentication" in parts:
        interface.ospf_auth = parts[-1]


def _vlan_list(text: str) -> set[int]:
    """`10,20,30-32` を番号の集まりにする。"""
    values: set[int] = set()
    for part in text.replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            if start.isdigit() and end.isdigit():
                values.update(range(int(start), int(end) + 1))
        elif part.isdigit():
            values.add(int(part))
    return values


def _parse_routes(text: str, parsed: ParsedConfig) -> None:
    """`ip route 0.0.0.0/0 10.0.10.254`。長さ付きなので IOS の読み手は拾えない。"""
    parsed.routes = [route for route in parsed.routes if route.destination]
    seen = {(route.destination, route.next_hop) for route in parsed.routes}
    for raw in text.splitlines():
        found = _ROUTE.match(raw.strip())
        if not found:
            continue
        destination = to_prefix(found.group("dest"))
        if destination is None:
            continue
        route = ParsedRoute(destination=destination)
        for token in found.group("rest").split():
            if re.match(r"^\d+\.\d+\.\d+\.\d+$", token):
                route.next_hop = token
            elif token.isdigit():
                route.distance = int(token)
            elif re.match(r"^[A-Za-z0-9]+/", token) or token.lower().startswith("vlan"):
                route.interface = token
        if (route.destination, route.next_hop) in seen:
            continue
        seen.add((route.destination, route.next_hop))
        parsed.routes.append(route)


def _parse_acls(text: str, parsed: ParsedConfig) -> None:
    """`access-list ip 名前` の並びと、ポートへの当て方。"""
    acls: dict[str, ParsedAcl] = {}
    current: ParsedAcl | None = None
    interface: str | None = None

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line:
            continue
        stripped = line.strip()

        if not line.startswith(" "):
            head = _ACL_HEAD.match(stripped)
            if head:
                current = acls.setdefault(head.group(1), ParsedAcl(name=head.group(1)))
                interface = None
                continue
            port = _INTERFACE.match(stripped)
            current = None
            interface = " ".join(port.group(1).split()) if port else None
            continue

        if current is not None:
            entry = _acl_entry(stripped)
            if entry is not None:
                current.entries.append(entry)
            continue

        if interface is not None:
            applied = _APPLY.match(stripped)
            if applied:
                acl = acls.setdefault(applied.group(1), ParsedAcl(name=applied.group(1)))
                acl.applied_to.append(f"interface {interface} {applied.group(2).lower()}")

    parsed.acls.extend(acls.values())


def _acl_entry(line: str) -> ParsedAclEntry | None:
    """`10 permit tcp any 10.0.20.0/24 eq 443` の1行。"""
    found = _ACL_ENTRY.match(line)
    if not found:
        return None
    entry = ParsedAclEntry(
        seq=found.group("seq"),
        action=found.group("action").lower(),
        raw=line,
    )
    tokens = found.group("rest").split()
    if not tokens:
        return entry
    entry.protocol = tokens.pop(0).lower()
    entry.source, tokens = _address(tokens)
    entry.source_port, tokens = _port(tokens)
    entry.destination, tokens = _address(tokens)
    entry.destination_port, tokens = _port(tokens)
    if any(token.lower() == "log" for token in tokens):
        entry.log = True
    return entry


def _address(tokens: list[str]) -> tuple[str | None, list[str]]:
    """`any` / `10.0.0.0/24` / `host 10.0.0.1` のいずれか。"""
    if not tokens:
        return None, tokens
    head = tokens[0].lower()
    if head == "any":
        return "any", tokens[1:]
    if head == "host" and len(tokens) >= 2:
        return tokens[1], tokens[2:]
    if re.match(r"^\d+\.\d+\.\d+\.\d+(/\d+)?$", tokens[0]):
        return tokens[0], tokens[1:]
    return None, tokens


def _port(tokens: list[str]) -> tuple[str | None, list[str]]:
    """`eq 443` / `range 1024 2048`。無ければ触らない。"""
    if not tokens:
        return None, tokens
    head = tokens[0].lower()
    if head in ("eq", "gt", "lt", "neq") and len(tokens) >= 2:
        return f"{head} {tokens[1]}", tokens[2:]
    if head == "range" and len(tokens) >= 3:
        return f"range {tokens[1]} {tokens[2]}", tokens[3:]
    return None, tokens


def _parse_lldp_table(text: str, parsed: ParsedConfig) -> None:
    """`show lldp neighbor-info` の表。図の線はここから引く。

    列は LOCAL-PORT / CHASSIS-ID / PORT-ID / PORT-DESC / TTL / SYS-NAME。
    PORT-DESC に空白が入ることがあるので、TTL（数字）を目印に前後を分ける。
    """
    lines = text.splitlines()
    for index, line in enumerate(lines):
        if not _LLDP_HEADER.match(line):
            continue
        for row in lines[index + 1 :]:
            stripped = row.strip()
            if not stripped:
                break
            if stripped.startswith("-"):
                continue
            columns = stripped.split()
            if len(columns) < 5:
                break
            ttl_at = next((i for i in range(len(columns) - 1, 1, -1) if columns[i].isdigit()), None)
            if ttl_at is None or ttl_at + 1 >= len(columns):
                continue
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=columns[0],
                    remote_hostname=columns[ttl_at + 1],
                    remote_interface=columns[2],
                    protocol=NeighborProtocol.LLDP,
                )
            )
