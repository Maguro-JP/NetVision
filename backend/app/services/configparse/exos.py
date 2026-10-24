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

出典: VLAN を番号の並びで書く形と description-string は ExtremeXOS Command Reference Guide
（22.1 / 30.1 / 31.7）で、束ね（enable sharing grouping）は v33.1.1 で、経路
（configure iproute add の IPv4・IPv6・default）と disable port は 22.5 で、VLAN の住所
（configure vlan ipaddress）は 32.4 で確かめた。
LLDP は `_lldp_detail` の docstring。
ほかの行は既存の試験の形のまま（未確認）。`disable port all` は読まない。
"""

from __future__ import annotations

import re

from app.services.configparse.base import (
    NeighborProtocol,
    ParsedConfig,
    ParsedInterface,
    ParsedNeighbor,
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
#: VLAN の住所。`vlan` は省ける（名前で書いたとき）。出典: ExtremeXOS Command Reference Guide
#: 32.4 の configure vlan ipaddress（`configure [{vlan} vlan_name | vlan vlan_id] ipaddress ...`）。
#: 省いた形の名前は、作った VLAN の名前に当たったときだけ使う（numbers_of）
_VLAN_IP = re.compile(
    r'^\s*configure\s+(?:vlan\s+)?"?(?P<name>[^"\s]+)"?\s+ipaddress\s+'
    r"(?P<ip>\d+\.\d+\.\d+\.\d+)[\s/](?P<mask>\d+\.\d+\.\d+\.\d+|\d{1,2})(?![\d.])",
    re.IGNORECASE,
)
#: 口の名前。`display-string` は短い表示名、`description-string` は長い説明（ifAlias）。
#: 出典: ExtremeXOS Command Reference Guide 30.1 の一覧（configure port description-string /
#: configure ports display-string）。両方あれば長い方を説明に持つ
_DISPLAY = re.compile(
    r"^\s*configure\s+ports?\s+(?P<ports>[\d\-,:]+)\s+(?P<kind>display|description)-string\s+"
    r'"?(?P<name>[^"\n]+?)"?\s*$',
    re.IGNORECASE,
)
#: VLAN を番号の並びで書いた形（`configure vlan 10,20 add ports 5 tagged`）。
#: 出典: ExtremeXOS Command Reference Guide 22.1 / 31.7 の configure vlan add ports
#: （`configure [{vlan} vlan_name | vlan vlan_list] add ports ...`、vlan_list は 16.1 から）
_VLAN_LIST = re.compile(r"[\d,\-]+")
_DISABLE = re.compile(r"^\s*disable\s+ports?\s+(?P<ports>[\d\-,:]+)", re.IGNORECASE)
#: 束ね。出典: ExtremeXOS v33.1.1 Command References の enable sharing grouping
#: （port は代表の口、port_list は束ねる口。末尾の lacp で動的、health-check か無しで静的）
_SHARING = re.compile(
    r"^\s*enable\s+sharing\s+(?P<master>[\d:]+)\s+grouping\s+(?P<ports>[\d\-,:]+)", re.IGNORECASE
)
_ROUTE = re.compile(
    r"^\s*configure\s+iproute\s+add\s+(?:(?P<default>default)|"
    r"(?P<dest>\d+\.\d+\.\d+\.\d+)[\s/](?P<mask>\d+\.\d+\.\d+\.\d+|\d{1,2})(?![\d.]))\s+(?P<hop>\d+\.\d+\.\d+\.\d+)",
    re.IGNORECASE,
)

#: IPv6 の経路。既定経路は IPv6 の次の宛先も書ける。スコープ付きの次の宛先
#: （`fe80::1%vlan名`）はアドレスの部分だけを持つ。出典: ExtremeXOS 22.5 Command Reference の
#: configure iproute add default と configure iproute add (IPV6)
_ROUTE6 = re.compile(
    r"^\s*configure\s+iproute\s+add\s+(?:(?P<default>default)|(?P<dest>[0-9a-f:]+:[0-9a-f:]*/\d{1,3}))"
    r"\s+(?P<hop>[0-9a-f]*:[0-9a-f:]+)(?:%\S+)?",
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
            for number in _vlan_numbers(name):
                parsed.vlans.setdefault(number, "")
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

    #: description-string を書いた口。display-string で上書きしない
    described: set[str] = set()

    def numbers_of(vlan: str) -> list[int]:
        """名前なら番号を引き、番号の並びならそのままほどく。引けなければ空。"""
        listed = _vlan_numbers(vlan)
        if listed:
            return listed
        return [tags[vlan]] if vlan in tags else []

    for raw in text.splitlines():
        display = _DISPLAY.match(raw)
        if display:
            long_form = display.group("kind").lower() == "description"
            for name in _ports(display.group("ports")):
                if name in described and not long_form:
                    continue
                parsed.add_interface(ParsedInterface(name=name)).description = display.group("name")
                if long_form:
                    described.add(name)
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
            found_numbers = numbers_of(address.group("name"))
            if len(found_numbers) != 1:
                continue
            number = found_numbers[0]
            port = parsed.add_interface(ParsedInterface(name=f"Vlan{number}"))
            port.vlan = number
            port.ip_address = f"{address.group('ip')}/{_prefixlen(address.group('mask'))}"
            continue
        members = _ADD_PORTS.match(raw)
        if members:
            found_numbers = numbers_of(members.group("name"))
            if not found_numbers:
                continue
            for number in found_numbers:
                parsed.vlans.setdefault(number, "")
            # 書いていなければタグ無し。EXOS の既定はそちら
            tagged = (members.group("how") or "untagged").lower() == "tagged"
            for name in _ports(members.group("ports")):
                port = parsed.add_interface(ParsedInterface(name=name))
                for number in found_numbers:
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
            continue
        route = _ROUTE6.match(raw)
        if route:
            destination = "::/0" if route.group("default") else route.group("dest").lower()
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

    _lldp_detail(text, parsed)
    return parsed


#: `show lldp ... neighbors detailed` の口の行
_LLDP_PORT = re.compile(r"^\s*LLDP\s+Port\s+(\S+)\s+detected\s+\d+\s+neighbor", re.IGNORECASE)
#: その口で見えている隣の始まり
_LLDP_NEIGHBOR = re.compile(r"^\s*Neighbor:\s", re.IGNORECASE)
#: 値は引用符で囲まれて出る（`System Name: "SW1"`）
_LLDP_SYSTEM_NAME = re.compile(r'^\s*-?\s*System\s+Name\s*:\s*"?([^"]+?)"?\s*$', re.IGNORECASE)
_LLDP_PORT_ID = re.compile(r'^\s*Port\s+ID\s*:\s*"?([^"]+?)"?\s*$', re.IGNORECASE)


def _lldp_detail(text: str, parsed: ParsedConfig) -> None:
    """`show lldp neighbors detailed` から隣を読む。

    簡易表（`show lldp neighbors`）は相手を MAC でしか出さないので、
    名前の出る詳細表示のほうを読む。名乗っていない相手は作らない。

    出典: ExtremeXOS User Guide「show lldp neighbors」の実行例
    （`LLDP Port 1:2 detected 1 neighbor` / `Port ID : "1:1"` /
    `- System Name: "System name 1"`）。
    """
    local: str | None = None
    host: str | None = None
    port_id: str | None = None
    in_neighbor = False

    def flush() -> None:
        nonlocal host, port_id, in_neighbor
        if local and host:
            parsed.add_neighbor(
                ParsedNeighbor(
                    local_interface=local,
                    remote_hostname=host,
                    remote_interface=port_id,
                    protocol=NeighborProtocol.LLDP,
                )
            )
        host = None
        port_id = None
        in_neighbor = False

    for raw in text.splitlines():
        head = _LLDP_PORT.match(raw)
        if head:
            flush()
            local = head.group(1)
            continue
        if _LLDP_NEIGHBOR.match(raw):
            flush()
            in_neighbor = True
            continue
        if not in_neighbor:
            continue
        name = _LLDP_SYSTEM_NAME.match(raw)
        if name:
            host = name.group(1).strip()
            continue
        found = _LLDP_PORT_ID.match(raw)
        if found and port_id is None:
            port_id = found.group(1).strip()
    flush()


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


def _vlan_numbers(written: str) -> list[int]:
    """`10,20` や `10-12` の番号の並びをほどく。名前（数字で始まらない）なら空。"""
    if not _VLAN_LIST.fullmatch(written):
        return []
    numbers: list[int] = []
    for part in written.split(","):
        start, _, end = part.partition("-")
        if not start.isdigit() or (end and not end.isdigit()):
            return []
        low, high = int(start), int(end or start)
        if not 1 <= low <= high <= 4094:
            return []
        numbers.extend(range(low, high + 1))
    return numbers


def _group_number(master: str) -> int:
    return int(master.split(":")[-1])


def _prefixlen(mask: str) -> int:
    """`24` とも `255.255.255.0` とも書かれる。"""
    if "." not in mask:
        return int(mask)
    return sum(bin(int(part)).count("1") for part in mask.split("."))
